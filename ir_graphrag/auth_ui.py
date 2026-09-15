"""Streamlit login gate using the same password/session service as RAG."""

import base64
import json
from html import escape

import streamlit as st

from .auth import AuthService, load_users, InvalidCredentials, LoginBusy, LoginRateLimited


@st.cache_resource
def get_auth_service():
    return AuthService(load_users())


def download_json(label, data, file_name):
    # Inline downloads avoid Streamlit's unauthenticated /media file URLs.
    encoded = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')).decode('ascii')
    st.markdown(f'<a class="json-download" href="data:application/json;base64,{encoded}" '
                f'download="{escape(file_name, quote=True)}" target="_self">{escape(label)}</a>',
                unsafe_allow_html=True)


def logout():
    get_auth_service().logout(st.session_state.get('auth_token'))
    for key in list(st.session_state):
        if key != 'language':
            del st.session_state[key]


def _login():
    username = st.session_state.get('login_username', '').strip()
    password = st.session_state.pop('login_password', '')
    try:
        token = get_auth_service().login(username, password, st.context.ip_address or 'local')
    except (InvalidCredentials, LoginBusy, LoginRateLimited) as error:
        st.session_state['login_error'] = str(error)
    else:
        logout()
        # shortcut: keep the token server-side; refresh/new tabs need a new login.
        st.session_state['auth_token'] = token


def require_login(t):
    try:
        service = get_auth_service()
    except ValueError:
        st.error(t('Login is not configured. Contact the administrator.'))
        st.stop()
    username = service.current_user(st.session_state.get('auth_token'))
    if username:
        return username
    if 'auth_token' in st.session_state:
        logout()
    with st.container(key='login-card'):
        st.title(t('Log in'))
        st.caption(t('Log in to view documents and use GraphRAG.'))
        with st.form('login'):
            st.text_input(t('Username'), key='login_username', max_chars=100)
            st.text_input(t('Password'), key='login_password', type='password', max_chars=1024)
            st.form_submit_button(t('Log in'), key='login_submit', type='primary', on_click=_login)
        if st.session_state.get('login_error'):
            st.error(t(st.session_state['login_error']))
    st.stop()
