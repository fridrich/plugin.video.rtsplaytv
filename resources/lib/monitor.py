# Copyright (C) 2026 Fridrich Strba
#
# This file is part of plugin.video.rtsplaytv.
#
# plugin.video.rtsplaytv is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import os
import sys
import time
import requests
import xbmc

# Insert resources/lib into path to import auth.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from auth import RTSAuth


class RTSPlaybackMonitor(xbmc.Player):
    """Monitors background playback progress and reports positions periodically to PEACH APIs."""

    def __init__(self, urn, title, cookies_path):
        xbmc.Player.__init__(self)
        self.urn = urn
        self.title = title
        self.playback_active = False
        self.last_position = 0
        self.auth = RTSAuth(session_file=cookies_path)

    def onPlayBackStarted(self):
        self.playback_active = True
        self.send_progress(0)

    def onPlayBackStopped(self):
        self.playback_active = False
        self.send_progress(self.last_position)

    def onPlayBackEnded(self):
        self.playback_active = False
        self.send_progress(self.last_position)

    def onPlayBackPaused(self):
        self.send_progress(self.last_position)

    def onPlayBackResumed(self):
        self.send_progress(self.last_position)

    def send_progress(self, position):
        cookies = self.auth.get_cookies()
        if not cookies:
            xbmc.log("RTSPlaybackMonitor: Skipping progress upload - cookies not found or empty.", xbmc.LOGDEBUG)
            return

        url = "https://profil.rts.ch/api/history/v2"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Referer": "https://www.rts.ch/"
        }

        item_id = self.urn
        if ":" not in item_id:
            item_id = f"urn:rts:video:{item_id}"

        payload = {
            "item_id": item_id,
            "last_playback_position": float(position),
            "device_id": "srg-player",
            "deleted": False,
            "date": int(time.time() * 1000)
        }

        try:
            res = requests.post(url, json=payload, headers=headers, cookies=cookies, timeout=10)
            xbmc.log(f"RTSPlaybackMonitor: Sent progress {position}s. Status: {res.status_code}", xbmc.LOGDEBUG)
        except Exception as e:
            xbmc.log(f"RTSPlaybackMonitor: Failed to send progress: {e}", xbmc.LOGDEBUG)


def main():
    if len(sys.argv) < 4:
        xbmc.log("RTSPlaybackMonitor: Missing arguments. Expected URN, Title, and Cookies Path.", xbmc.LOGERROR)
        return
    urn = sys.argv[1]
    title = sys.argv[2]
    cookies_path = sys.argv[3]

    xbmc.log(f"RTSPlaybackMonitor: Starting for {urn} ({title})", xbmc.LOGINFO)
    xbmc.log(f"RTSPlaybackMonitor: Cookies path: {cookies_path}", xbmc.LOGDEBUG)

    monitor = RTSPlaybackMonitor(urn, title, cookies_path)

    # Wait until playback starts or times out
    timeout = 15
    while not monitor.playback_active and timeout > 0:
        xbmc.sleep(1000)
        timeout -= 1
        if monitor.isPlayingVideo():
            monitor.playback_active = True
            break

    # Main tracking loop
    last_report_time = time.time()
    while monitor.playback_active or monitor.isPlayingVideo():
        if xbmc.Monitor().abortRequested():
            break
        xbmc.sleep(1000)
        if monitor.isPlayingVideo():
            try:
                pos = int(monitor.getTime())
                if pos > 0:
                    monitor.last_position = pos
            except Exception:
                pass

            now = time.time()
            if now - last_report_time >= 30:
                monitor.send_progress(monitor.last_position)
                last_report_time = now

    xbmc.log("RTSPlaybackMonitor: Finished monitoring", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
