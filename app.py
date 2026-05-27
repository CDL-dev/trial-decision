"""Streamlit entry point for the simplified trial app."""

import sqlite3

import streamlit as st

from streamlit_app.config import APP_TITLE, DB_PATH
from streamlit_app.db import bootstrap_db
from streamlit_app.services.current_match_service import get_match_phase, get_current_match
from streamlit_app.services.match_service import delete_match
from streamlit_app.services.player_service import get_player
from streamlit_app.ui.admin.setup_page import render as admin_setup
from streamlit_app.ui.admin.setup_confirm_page import render as admin_confirm
from streamlit_app.ui.admin.control_panel_page import render as admin_control
from streamlit_app.ui.player.login_page import render as player_login
from streamlit_app.ui.player.onboarding_page import render as player_onboarding
from streamlit_app.ui.player.decision_page import render as player_decision
from streamlit_app.ui.player.waiting_page import render as player_waiting
from streamlit_app.ui.player.report_page import render as player_report
from streamlit_app.ui.player.final_result_page import render as player_final


def _get_player_route(db_path, player_id: int) -> str:
    """Determine which page a logged-in player should see."""
    player = get_player(db_path, player_id)
    if not player:
        return "login"

    if not player["setup_completed"]:
        return "onboarding"

    match = get_current_match(db_path)
    if not match:
        return "login"

    status = match["status"]
    if status == "setup":
        return "waiting"

    if status == "ended":
        return "final"

    # status == "running"
    current_round = match["current_round"]
    conn = sqlite3.connect(db_path)
    submitted = conn.execute(
        "SELECT 1 FROM round_submissions WHERE match_id = ? AND round_index = ? AND player_id = ?",
        (match["id"], current_round, player_id),
    ).fetchone()
    has_report = conn.execute(
        "SELECT 1 FROM round_results WHERE match_id = ? AND round_index = ? AND player_id = ?",
        (match["id"], current_round - 1, player_id),
    ).fetchone()
    conn.close()

    if has_report and not submitted:
        return "playing"
    if submitted:
        return "waiting"
    return "decision"


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    # Bootstrap DB
    bootstrap_db(DB_PATH)

    # Initialize session
    if "player_id" not in st.session_state:
        st.session_state["player_id"] = None

    st.title(APP_TITLE)
    mode = st.sidebar.radio("Workspace", ["Admin", "Player"])

    if mode == "Admin":
        _render_admin()
    else:
        _render_player()


def _render_admin():
    phase = get_match_phase(DB_PATH)

    if phase == "empty":
        admin_setup(DB_PATH)
    elif phase == "setup":
        admin_confirm(DB_PATH)
    elif phase == "running":
        admin_control(DB_PATH)
    elif phase == "ended":
        st.header("Match Ended")
        match = get_current_match(DB_PATH)
        if match:
            st.info(f"Match '{match['name']}' has ended.")
            st.divider()
            with st.expander("Danger Zone", expanded=False):
                st.warning("Deleting this match will remove all data.")
                confirmed = st.checkbox("I confirm I want to delete this match and all its data")
                if st.button("Delete Match", type="secondary", disabled=not confirmed):
                    delete_match(DB_PATH, match["id"])
                    st.success("Match deleted. Create a new match to continue.")
                    st.rerun()
        # Show final results
        st.session_state["player_id"] = None
        player_final(DB_PATH)


def _render_playing_workspace():
    """Running state with report available: toggle between Report and Decision."""
    if "player_view" not in st.session_state:
        st.session_state["player_view"] = "Report"

    view = st.radio(
        "View", ["Report", "Decision"],
        index=0 if st.session_state["player_view"] == "Report" else 1,
        horizontal=True, key="player_view",
    )

    if view == "Report":
        player_report(DB_PATH)
        if st.session_state.get("force_final"):
            st.session_state["force_final"] = False
    else:
        player_decision(DB_PATH)


def _render_player():
    player_id = st.session_state.get("player_id")

    if not player_id:
        # Logout button if previously logged in
        player_login(DB_PATH)
        return

    # Determine route
    route = _get_player_route(DB_PATH, player_id)

    # Sidebar: show player info + logout
    player = get_player(DB_PATH, player_id)
    if player:
        with st.sidebar:
            st.caption(f"Player {player['player_no']}: {player['company_name']}")
            if st.button("Logout"):
                st.session_state["player_id"] = None
                st.rerun()

    if route == "login":
        st.session_state["player_id"] = None
        st.rerun()
    elif route == "onboarding":
        player_onboarding(DB_PATH)
    elif route == "waiting":
        player_waiting(DB_PATH)
    elif route == "playing":
        _render_playing_workspace()
    elif route == "decision":
        player_decision(DB_PATH)
    elif route == "report":
        player_report(DB_PATH)
        if st.session_state.get("force_final"):
            st.session_state["force_final"] = False
    elif route == "final":
        player_final(DB_PATH)


if __name__ == "__main__":
    main()
