"""Tests for the orchestrator and agents."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.state import AgentState, Phase, Subtask
from src.agents.planner import PlannerAgent
from src.agents.coder import CoderAgent
from src.agents.reviewer import ReviewerAgent


class TestState:
    def test_initial_state(self):
        state = AgentState(task="add dark mode toggle")
        assert state.task == "add dark mode toggle"
        assert state.current_phase == Phase.PLANNING
        assert state.subtasks == []
        assert state.files_written == []
        assert state.pr_url == ""

    def test_log_appends_message(self):
        state = AgentState(task="test")
        state.log("hello from planner", agent="planner")
        assert len(state.messages) == 1
        assert state.messages[0].content == "hello from planner"
        assert state.messages[0].agent == "planner"

    def test_log_calls_progress_cb(self):
        events = []
        state = AgentState(task="test", progress_cb=lambda e: events.append(e))
        state.log("progress update", agent="coder")
        assert len(events) == 1
        assert events[0]["agent"] == "coder"

    def test_project_file_list_no_dir(self):
        state = AgentState(task="test")
        result = state.project_file_list()
        assert "No project directory" in result

    def test_project_file_list_missing_dir(self):
        state = AgentState(task="test", project_dir="/nonexistent/path")
        result = state.project_file_list()
        assert "does not exist" in result


class TestPlannerAgent:
    @patch("src.agents.base.call_json")
    def test_planner_creates_subtasks(self, mock_call_json):
        mock_call_json.return_value = {
            "plan_summary": "Add dark mode support",
            "subtasks": [
                {"id": 1, "description": "Create ThemeContext", "files_to_touch": ["contexts/theme.tsx"]},
                {"id": 2, "description": "Update settings screen", "files_to_touch": ["app/settings.tsx"]},
            ]
        }
        planner = PlannerAgent()
        state = AgentState(task="add dark mode")
        state = planner.run(state)

        assert len(state.subtasks) == 2
        assert state.subtasks[0].description == "Create ThemeContext"
        assert state.subtasks[0].files_to_touch == ["contexts/theme.tsx"]
        assert state.current_phase == Phase.PLANNING
        assert state.plan_summary == "Add dark mode support"


class TestCoderAgent:
    @patch("src.tools.filesystem.write_file")
    @patch("src.tools.filesystem.read_multiple_files", return_value="")
    @patch("src.agents.base.call_json")
    def test_coder_writes_files(self, mock_call_json, mock_read, mock_write):
        mock_call_json.return_value = {
            "files": {"contexts/theme.tsx": "export const ThemeContext = React.createContext({});"},
            "summary": "Created ThemeContext",
        }
        mock_write.return_value = "/project/contexts/theme.tsx"

        coder = CoderAgent()
        state = AgentState(task="add dark mode", project_dir="/project")
        subtask = Subtask(id=1, description="Create ThemeContext", files_to_touch=["contexts/theme.tsx"])

        state = coder.run(state, subtask=subtask)

        assert "contexts/theme.tsx" in state.files_written
        mock_write.assert_called_once()

    @patch("src.tools.filesystem.write_file")
    @patch("src.tools.filesystem.read_multiple_files", return_value="")
    @patch("src.agents.base.call_json")
    def test_coder_strips_markdown_fences(self, mock_call_json, mock_read, mock_write):
        mock_call_json.return_value = {
            "files": {"index.tsx": "```tsx\nconst x = 1;\n```"},
            "summary": "Done",
        }
        mock_write.return_value = "/project/index.tsx"

        coder = CoderAgent()
        state = AgentState(task="test", project_dir="/project")
        subtask = Subtask(id=1, description="test", files_to_touch=["index.tsx"])
        coder.run(state, subtask=subtask)

        written_content = mock_write.call_args[0][2]
        assert "```" not in written_content


class TestReviewerAgent:
    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_reviewer_approves(self, mock_call_json, mock_read):
        mock_call_json.return_value = {
            "approved": True,
            "feedback": "",
            "summary": "Code looks solid",
        }

        reviewer = ReviewerAgent()
        state = AgentState(task="test", project_dir="/project")
        subtask = Subtask(id=1, description="Create component", files_to_touch=["comp.tsx"])

        state = reviewer.run(state, subtask=subtask)

        assert subtask.status == "done"
        assert "APPROVED" in subtask.review_feedback

    @patch("src.tools.filesystem.read_multiple_files", return_value="const x = 1;")
    @patch("src.agents.base.call_json")
    def test_reviewer_requests_revision(self, mock_call_json, mock_read):
        mock_call_json.return_value = {
            "approved": False,
            "feedback": "Missing TypeScript types",
            "summary": "Needs improvement",
        }

        reviewer = ReviewerAgent()
        state = AgentState(task="test", project_dir="/project")
        subtask = Subtask(id=1, description="test", files_to_touch=["comp.tsx"])

        state = reviewer.run(state, subtask=subtask)

        assert subtask.status == "pending"
        assert "Missing TypeScript types" in subtask.review_feedback
        assert subtask.revision_count == 1

