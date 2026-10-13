"""Compatibility entry point for the LangGraph research workflow."""

from agent.langgraph.main import (
    REPORT_DIR,
    _fix_windows_console,
    _print_result,
    _safe_filename,
    _save_report,
    _solve,
    main,
)


if __name__ == "__main__":
    main()
