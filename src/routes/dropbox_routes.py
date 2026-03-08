"""
BriefDrafter Dropbox integration: OAuth routes + client helpers.
"""

import json
from flask import Blueprint, request, redirect, session
import dropbox
from dropbox.exceptions import ApiError, AuthError

from src.config import DROPBOX_APP_KEY, DROPBOX_APP_SECRET, CONFIG_PATH, config

dropbox_bp = Blueprint('dropbox', __name__)


@dropbox_bp.route('/dropbox/auth')
def dropbox_auth():
    """Start Dropbox OAuth flow."""
    if not DROPBOX_APP_KEY or not DROPBOX_APP_SECRET:
        return "Dropbox App Key and Secret not configured. Add them to config.json first.", 400

    auth_flow = dropbox.DropboxOAuth2Flow(
        consumer_key=DROPBOX_APP_KEY,
        consumer_secret=DROPBOX_APP_SECRET,
        redirect_uri="http://127.0.0.1:5003/dropbox/callback",
        session=session,
        csrf_token_session_key="dropbox-auth-csrf-token",
        token_access_type='offline'
    )
    authorize_url = auth_flow.start()
    return redirect(authorize_url)


@dropbox_bp.route('/dropbox/callback')
def dropbox_callback():
    """Handle Dropbox OAuth callback."""
    try:
        auth_flow = dropbox.DropboxOAuth2Flow(
            consumer_key=DROPBOX_APP_KEY,
            consumer_secret=DROPBOX_APP_SECRET,
            redirect_uri="http://127.0.0.1:5003/dropbox/callback",
            session=session,
            csrf_token_session_key="dropbox-auth-csrf-token",
            token_access_type='offline'
        )
        oauth_result = auth_flow.finish(request.args)

        # Save tokens
        config['dropbox_access_token'] = oauth_result.access_token
        if oauth_result.refresh_token:
            config['dropbox_refresh_token'] = oauth_result.refresh_token
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)

        return redirect('/?dropbox=connected')
    except Exception as e:
        return f"Error connecting to Dropbox: {str(e)}", 400


def get_dropbox_client():
    """Get an authenticated Dropbox client with automatic token refresh."""
    access_token = config.get('dropbox_access_token')
    refresh_token = config.get('dropbox_refresh_token')
    app_key = config.get('dropbox_app_key')
    app_secret = config.get('dropbox_app_secret')

    if not access_token:
        return None

    if refresh_token and app_key and app_secret:
        return dropbox.Dropbox(
            oauth2_access_token=access_token,
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret
        )

    return dropbox.Dropbox(access_token)


def get_dropbox_shared_link(filename):
    """Get or create a shared link for a file in Dropbox."""
    dbx = get_dropbox_client()
    if not dbx:
        return None

    folder_path = config.get('dropbox_folder_path', '')
    if not folder_path:
        return None

    if not folder_path.startswith('/'):
        folder_path = '/' + folder_path
    file_path = f"{folder_path.rstrip('/')}/{filename}"

    try:
        links = dbx.sharing_list_shared_links(path=file_path, direct_only=True).links
        if links:
            return links[0].url

        shared_link = dbx.sharing_create_shared_link_with_settings(file_path)
        return shared_link.url
    except AuthError as e:
        print(f"[DROPBOX] Auth error - token expired or invalid: {e}")
        print("[DROPBOX] Please reconnect Dropbox in Settings")
        return None
    except ApiError as e:
        print(f"[DROPBOX] Error getting link for {file_path}: {e}")
        return None
