"""Streamlit entry point for the simplified trial app."""

from streamlit_app.config import APP_TITLE


def main():
    import streamlit as st

    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.info("App skeleton initialized.")


if __name__ == "__main__":
    main()
