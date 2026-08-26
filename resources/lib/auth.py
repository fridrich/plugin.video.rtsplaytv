# Copyright (C) 2026 Fridrich Strba
#
# This file is part of plugin.video.rtsplaytv.
#
# plugin.video.rtsplaytv is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import http.cookiejar
import os
import re
import requests

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcvfs
    KODI_AVAILABLE = True
except ImportError:
    KODI_AVAILABLE = False

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None


def log_msg(msg, level=None):
    if KODI_AVAILABLE:
        if level is None:
            level = xbmc.LOGDEBUG
        xbmc.log(f"RTSAuth: {msg}", level)


class RTSAuth:
    """Manages the MaRTS proprietary cookie-session login and standard LWPCookieJar caching."""

    LOGIN_URL = "https://www.rts.ch/profile/login/?redirect=https://www.rts.ch/"

    IMPERSONATE_TARGETS = ("chrome120", "chrome124", "edge101", "safari180")
    # Fallback UA, used only when no curl_cffi target is available at all
    # (plain requests.Session() has no impersonation of its own).
    FALLBACK_USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, addon=None, session_file=None):
        self.addon = addon
        if session_file:
            self.session_file = session_file
        else:
            profile_dir = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
            if not os.path.exists(profile_dir):
                try:
                    os.makedirs(profile_dir)
                except Exception:
                    pass
            self.session_file = os.path.join(profile_dir, "cookies.lwp")

    def prompt_credentials_and_login(self):
        """Prompts the user interactively and authenticates, caching the cookie jar."""
        if not KODI_AVAILABLE or not self.addon:
            return False

        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

        email = self.addon.getSetting("email")
        password = self.addon.getSetting("password")

        email_label = self.addon.getLocalizedString(30080) or "Email"
        password_label = self.addon.getLocalizedString(30081) or "Password"

        # Prompt for Email
        keyboard = xbmc.Keyboard(email, email_label)
        keyboard.doModal()
        if not keyboard.isConfirmed():
            return False
        email = keyboard.getText().strip()
        if not email:
            return False

        # Prompt for Password
        keyboard = xbmc.Keyboard(password, password_label, True)
        keyboard.doModal()
        if not keyboard.isConfirmed():
            return False
        password = keyboard.getText()
        if not password:
            return False

        # Re-activate busy dialog during network login handshake
        xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
        try:
            self._login_with_credentials(email, password)
            self.addon.setSetting("email", email)
            return True
        finally:
            self.addon.setSetting("password", "")
            xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    def get_cookies(self):
        """Returns standard LWPCookieJar with cached valid session cookies or None."""
        cookie_jar = http.cookiejar.LWPCookieJar(self.session_file)
        if os.path.exists(self.session_file):
            try:
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                if len(list(cookie_jar)) > 0:
                    return cookie_jar
                else:
                    log_msg("Cookie jar is empty.", xbmc.LOGDEBUG if KODI_AVAILABLE else None)
            except Exception as e:
                log_msg(f"Error reading session cache: {e}", xbmc.LOGDEBUG if KODI_AVAILABLE else None)
        else:
            log_msg(f"Cookie jar file not found at: {self.session_file}", xbmc.LOGDEBUG if KODI_AVAILABLE else None)
        return None

    def _login_with_credentials(self, email, password):
        """Executes the MaRTS proprietary login handshake."""
        cookie_jar = http.cookiejar.LWPCookieJar(self.session_file)
        if os.path.exists(self.session_file):
            try:
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        def prep_session(candidate):
            # Plain 2-arg set(), not .update(cookie_jar) -- that's a
            # requests-specific extension curl_cffi may not have.
            for cookie in cookie_jar:
                candidate.cookies.set(cookie.name, cookie.value)
            candidate.headers.update(headers)
            return candidate

        # Step 1: GET login page to obtain request_identifier, request_date
        # and initial cookies. Impersonation target-fallback loop wraps this
        # specific request (not just Session construction) -- ImpersonateError
        # only surfaces on first real use of a target, never at construction.
        log_msg("Step 1: Contacting login server (GET)...")
        session = None
        res = None
        for target in self.IMPERSONATE_TARGETS if curl_requests else ():
            log_msg(f"Trying to impersonate {target}")
            try:
                candidate = prep_session(curl_requests.Session(impersonate=target))
                res = candidate.get(self.LOGIN_URL, timeout=15)
                session = candidate
                break
            except Exception as e:
                log_msg(f"{target} failed: {e}")
                continue
        if session is None:
            log_msg("Using standard requests library...")
            session = prep_session(requests.Session())
            session.headers["User-Agent"] = self.FALLBACK_USER_AGENT
            res = session.get(self.LOGIN_URL, timeout=15)
        if not res.ok:
            raise Exception("FAILED_GET_LOGIN_PAGE")

        html = res.text
        id_match = re.search(r'<input[^>]+id=["\']request_identifier["\'][^>]+value=["\']([^"\']+)["\']', html)
        if not id_match:
            id_match = re.search(r'<input[^>]+value=["\']([^"\']+)["\'][^>]+id=["\']request_identifier["\']', html)

        date_match = re.search(r'<input[^>]+id=["\']request_date["\'][^>]+value=["\']([^"\']+)["\']', html)
        if not date_match:
            date_match = re.search(r'<input[^>]+value=["\']([^"\']+)["\'][^>]+id=["\']request_date["\']', html)

        if not id_match or not date_match:
            log_msg(f"Form elements missing from page: {html[:1000]}", xbmc.LOGERROR if KODI_AVAILABLE else None)
            raise Exception("FORM_MARKUP_CHANGED")

        request_identifier = id_match.group(1)
        request_date = date_match.group(1)

        # Step 2: POST credentials to the same URL
        log_msg("Step 2: Submitting credentials (POST)...")
        data = {
            "marts_action": "login",
            "request_identifier": request_identifier,
            "request_date": request_date,
            "request_marts_login_field": "",  # honeypot
            "login_email": email,
            "login_password": password
        }

        res_post = session.post(self.LOGIN_URL, data=data, allow_redirects=True, timeout=15)

        # No domain= kwarg (requests-specific, may not exist on curl_cffi)
        # -- safe since only one .rts.ch cookie has this name.
        cookies = session.cookies
        sid_cookie = cookies.get("identity.provider.sid")

        if not sid_cookie:
            for resp in res_post.history:
                sid_cookie = resp.cookies.get("identity.provider.sid")
                if sid_cookie:
                    break

        if not sid_cookie:
            # Check if there is a known error message on the page
            if "Nom d'utilisateur ou mot de passe incorrect." in res_post.text or "marts-form-error-message" in res_post.text:
                raise Exception("PASSWORD_INVALID")
            raise Exception("LOGIN_FAILED")

        # Save all cookies to LWPCookieJar
        cookie_jar.clear()
        for cookie in session.cookies:
            cookie_jar.set_cookie(cookie)

        cookie_jar.save(ignore_discard=True, ignore_expires=True)
        return True
