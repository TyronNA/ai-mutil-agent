"""Tests for the game agent pipeline (DevAgent, QAAgent, TechExpertAgent, GameAgentState)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.agents.dev import DevAgent
from src.agents.qa import QAAgent
from src.agents.tech_expert import TechExpertAgent
from src.tools.js_ast_patch import apply_ast_patch


class TestGameState:
    def test_initial_state(self):
        state = GameAgentState(task="add daily reward popup", game_project_dir="")
        assert state.task == "add daily reward popup"
        assert state.current_phase == GamePhase.LOADING
        assert state.subtasks == []
        assert state.files_written == []
        assert state.pr_url == ""

    def test_log_appends_message(self):
        state = GameAgentState(task="test", game_project_dir="")
        state.log("hello from dev", agent="dev")
        assert len(state.messages) == 1
        assert state.messages[0]["message"] == "hello from dev"
        assert state.messages[0]["agent"] == "dev"

    def test_log_calls_progress_cb(self):
        events = []
        state = GameAgentState(task="test", game_project_dir="", progress_cb=lambda e: events.append(e))
        state.log("progress update", agent="dev")
        assert len(events) == 1
        assert events[0]["agent"] == "dev"

    def test_game_file_list_no_dir(self):
        state = GameAgentState(task="test", game_project_dir="")
        result = state.game_file_list()
        assert "No game project" in result

    def test_game_file_list_missing_dir(self):
        state = GameAgentState(task="test", game_project_dir="/nonexistent/game")
        result = state.game_file_list()
        assert "does not exist" in result


class TestTechExpertAgent:
    @patch("src.agents.base.call_json")
    def test_plan_creates_subtasks(self, mock_call_json):
        mock_call_json.return_value = {
            "implementation_plan": "Add reward popup with gold/stamina",
            "subtasks": [
                {"id": 1, "description": "Create DailyRewardScene", "files_to_touch": ["src/scenes/DailyRewardScene.js"]},
                {"id": 2, "description": "Register scene in config", "files_to_touch": ["src/config.js"]},
            ],
            "test_scenarios": ["Popup shows gold amount"],
            "global_constraints": ["Use UI_THEME for colors"],
        }
        agent = TechExpertAgent()
        state = GameAgentState(task="add daily reward popup", game_project_dir="")
        state = agent.plan(state)

        assert len(state.subtasks) == 2
        assert state.subtasks[0].description == "Create DailyRewardScene"
        assert state.subtasks[0].files_to_touch == ["src/scenes/DailyRewardScene.js"]
        assert state.current_phase == GamePhase.PLANNING
        assert "reward popup" in state.implementation_plan
        assert len(state.test_scenarios) == 1
        assert len(state.global_constraints) == 1

    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_review_approved(self, mock_call_json, mock_read):
        mock_call_json.return_value = {
            "verdict": "approved",
            "notes": "All conventions followed",
            "specific_issues": [],
        }
        agent = TechExpertAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        state.files_written = ["src/scenes/DailyRewardScene.js"]
        state = agent.review(state)

        assert state.review_verdict == "approved"
        assert state.review_specific_issues == []

    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_review_flags_issues(self, mock_call_json, mock_read):
        mock_call_json.return_value = {
            "verdict": "needs_revision",
            "notes": "CombatEngine violation",
            "specific_issues": [
                "src/classes/CombatEngine.js > applyDamage() line ~42: imports Phaser directly — fix: remove import"
            ],
        }
        agent = TechExpertAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        state.files_written = ["src/classes/CombatEngine.js"]
        state = agent.review(state)

        assert state.review_verdict == "needs_revision"
        assert len(state.review_specific_issues) == 1

    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_review_escalates_to_pro_on_complex_change_set(self, mock_call_json, mock_read):
        mock_call_json.return_value = {
            "verdict": "approved",
            "notes": "ok",
            "specific_issues": [],
        }
        agent = TechExpertAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        state.files_written = [
            "src/classes/CombatEngine.js",
            "src/classes/StatusProcessor.js",
            "src/classes/PassiveRegistry.js",
            "src/scenes/BattleScene.js",
        ]

        agent.review(state)

        assert mock_call_json.call_args.kwargs["pro"] is True
        assert mock_call_json.call_args.kwargs["thinking_budget"] == 2048


class TestDevAgent:
    @patch("src.agents.dev.write_file")
    @patch("src.agents.dev.read_multiple_files", return_value="")
    @patch("src.agents.dev.read_file", return_value="")
    @patch("src.llm.create_cache", return_value="")
    @patch("src.agents.base.call_json")
    def test_dev_applies_patches(self, mock_call_json, mock_cache, mock_read_file, mock_read_multi, mock_write):
        mock_call_json.return_value = {
            "patches": [{"file": "src/scenes/MainScene.js", "find": "old code", "replace": "new code"}],
            "new_files": {},
            "summary": "Applied patch",
        }
        # Seed original_files so patch has a base to apply against
        dev = DevAgent()
        state = GameAgentState(task="fix bug", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="Fix bug in MainScene", files_to_touch=["src/scenes/MainScene.js"])
        subtask.original_files["src/scenes/MainScene.js"] = "const x = old code; // rest"

        dev.run(state, subtask=subtask)

        assert "src/scenes/MainScene.js" in state.files_written
        mock_write.assert_called_once()

    @patch("src.agents.dev.write_file")
    @patch("src.agents.dev.read_multiple_files", return_value="")
    @patch("src.agents.dev.read_file", return_value="")
    @patch("src.llm.create_cache", return_value="")
    @patch("src.agents.base.call_json")
    def test_dev_new_file_strips_markdown(self, mock_call_json, mock_cache, mock_read_file, mock_read_multi, mock_write):
        mock_call_json.return_value = {
            "patches": [],
            "new_files": {"src/scenes/NewScene.js": "```js\nconst x = 1;\n```"},
            "summary": "Created new scene",
        }
        dev = DevAgent()
        state = GameAgentState(task="add scene", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="Create new scene", files_to_touch=["src/scenes/NewScene.js"])

        dev.run(state, subtask=subtask)

        written_content = mock_write.call_args[0][2]
        assert "```" not in written_content
        assert "const x = 1;" in written_content

    def test_apply_patches_fallback_line_ending_normalization(self):
        base = "const a = 1;\r\nconst b = 2;\r\n"
        patches = [{
            "find": "const a = 1;\nconst b = 2;\n",
            "replace": "const a = 10;\nconst b = 2;\n",
        }]

        patched, warnings, failed = DevAgent._apply_patches(patches, base, "src/x.js")

        assert "const a = 10;" in patched
        assert warnings == []
        assert failed == []

    def test_apply_patches_ast_import_fallback(self):
        base = (
            "import { gotoScene } from '../utils/sceneTransition.js';\n"
            "const x = 1;\n"
        )
        patches = [{
            "find": "import {gotoScene} from \"../utils/sceneTransition.js\";",
            "replace": "import { gotoScene, foo } from '../utils/sceneTransition.js';",
        }]

        patched, warnings, failed = DevAgent._apply_patches(patches, base, "src/scenes/A.js")

        assert "gotoScene, foo" in patched
        assert failed == []
        # AST fallback may still append informational warnings for skipped earlier strategies.
        assert isinstance(warnings, list)


class TestJsAstPatch:
    def test_ast_patch_rejects_ambiguous_targets(self):
        base = "function init() { return 1; }\nfunction init() { return 2; }\n"
        find = "function init() { return 0; }"
        replace = "function init() { return 3; }"

        applied, _, reason = apply_ast_patch(base, find, replace)

        assert applied is False
        assert "ambiguous" in reason


class TestQAAgent:
    @patch("src.tools.game_tools.run_js_linter", return_value="no issues found")
    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_qa_passes_no_critical(self, mock_call_json, mock_read, mock_lint):
        mock_call_json.return_value = {
            "passed": True,
            "issues": [],
            "summary": "All conventions followed",
            "queue_suggestions": [],
        }
        qa = QAAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="Add reward scene", files_to_touch=["src/scenes/RewardScene.js"])
        subtask.written_files["src/scenes/RewardScene.js"] = "const x = 1;"

        qa.run(state, subtask=subtask)

        assert subtask.qa_passed is True
        assert subtask.qa_summary == "All conventions followed"

    @patch("src.tools.game_tools.run_js_linter", return_value="no issues found")
    @patch("src.tools.filesystem.read_multiple_files", return_value="import Phaser from 'phaser';")
    @patch("src.agents.base.call_json")
    def test_qa_fails_on_critical(self, mock_call_json, mock_read, mock_lint):
        mock_call_json.return_value = {
            "passed": False,
            "issues": [{
                "file": "src/classes/CombatEngine.js",
                "severity": "critical",
                "description": "CombatEngine imports Phaser directly",
            }],
            "summary": "Critical: CombatEngine purity violation",
            "queue_suggestions": [],
        }
        qa = QAAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="Add combat logic", files_to_touch=["src/classes/CombatEngine.js"])
        subtask.written_files["src/classes/CombatEngine.js"] = "import Phaser from 'phaser';"

        qa.run(state, subtask=subtask)

        assert subtask.qa_passed is False
        assert len([i for i in subtask.qa_issues if i["severity"] == "critical"]) == 1

    @patch("src.tools.game_tools.run_js_linter", return_value="no issues found")
    @patch("src.tools.filesystem.read_multiple_files", return_value="")
    @patch("src.agents.base.call_json")
    def test_qa_passes_with_only_warnings(self, mock_call_json, mock_read, mock_lint):
        mock_call_json.return_value = {
            "passed": True,
            "issues": [{
                "file": "src/scenes/BattleScene.js",
                "severity": "warning",
                "description": "Minor style issue",
            }],
            "summary": "Passed with 1 warning",
            "queue_suggestions": [],
        }
        qa = QAAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="test", files_to_touch=["src/scenes/BattleScene.js"])
        subtask.written_files["src/scenes/BattleScene.js"] = "const x = 1;"

        qa.run(state, subtask=subtask)

        # Warnings do NOT block passing
        assert subtask.qa_passed is True

    @patch("src.tools.game_tools.run_js_linter", return_value="no issues found")
    @patch("src.tools.filesystem.read_multiple_files", return_value="")
    @patch("src.agents.base.call_json")
    def test_qa_fails_on_blocking_warning(self, mock_call_json, mock_read, mock_lint):
        mock_call_json.return_value = {
            "passed": True,
            "issues": [{
                "file": "src/classes/SaveManager.js",
                "severity": "warning",
                "description": "SaveManager flow violation: localStorage accessed directly",
            }],
            "summary": "Passed with warning",
            "queue_suggestions": [],
        }
        qa = QAAgent()
        state = GameAgentState(task="test", game_project_dir="/project")
        subtask = GameSubtask(id=1, description="save flow", files_to_touch=["src/classes/SaveManager.js"])
        subtask.written_files["src/classes/SaveManager.js"] = "const x = 1;"

        qa.run(state, subtask=subtask)

        assert subtask.qa_passed is False
        assert "Escalated fail due to warning policy" in subtask.qa_summary

