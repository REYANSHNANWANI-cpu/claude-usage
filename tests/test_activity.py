"""Unit tests for Contextual Activity classification engine."""
import unittest

from claude_sentinel.activity_tracker import classify_interface


class TestActivityClassifier(unittest.TestCase):

    def test_claude_chat_classification(self):
        """Verify browser windows on claude.ai are classified as claude_chat."""
        # Chrome
        interface, label = classify_interface("chrome.exe", "Claude - Chat - Google Chrome")
        self.assertEqual(interface, "claude_chat")
        self.assertEqual(label, "Claude Web Chatting")

        # Edge
        interface, label = classify_interface("msedge.exe", "claude.ai/chat/a9b8c7 - Personal - Microsoft Edge")
        self.assertEqual(interface, "claude_chat")
        self.assertEqual(label, "Claude Web Chatting")

        # Firefox
        interface, label = classify_interface("firefox.exe", "Claude | Anthropic — Mozilla Firefox")
        self.assertEqual(interface, "claude_chat")

        # Brave
        interface, label = classify_interface("brave.exe", "claude.ai")
        self.assertEqual(interface, "claude_chat")

    def test_claude_code_cli_classification(self):
        """Verify terminal processes running claude CLI are classified as claude_code."""
        # Direct claude.exe
        interface, label = classify_interface("claude.exe", "claude")
        self.assertEqual(interface, "claude_code")
        self.assertEqual(label, "Claude Code (CLI Execution)")

        # PowerShell with claude in title
        interface, label = classify_interface("powershell.exe", "Administrator: claude")
        self.assertEqual(interface, "claude_code")

        # Windows Terminal
        interface, label = classify_interface("windowsterminal.exe", "claude-code session")
        self.assertEqual(interface, "claude_code")

    def test_idle_other_classification(self):
        """Verify generic non-Claude applications are classified as idle_other."""
        interface, label = classify_interface("notepad.exe", "Untitled - Notepad")
        self.assertEqual(interface, "idle_other")
        self.assertEqual(label, "Idle/Other Work")

        interface, label = classify_interface("explorer.exe", "Documents")
        self.assertEqual(interface, "idle_other")

        interface, label = classify_interface("spotify.exe", "Spotify Free")
        self.assertEqual(interface, "idle_other")


if __name__ == "__main__":
    unittest.main()
