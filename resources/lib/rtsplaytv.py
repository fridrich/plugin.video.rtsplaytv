# Copyright (C) 2018 Alexander Seiler
#
#
# This file is part of plugin.video.rtsplaytv.
#
# plugin.video.rtsplaytv is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# plugin.video.rtsplaytv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with plugin.video.rtsplaytv.
# If not, see <http://www.gnu.org/licenses/>.

import sys
import traceback
import urllib.request

from urllib.parse import unquote_plus
from urllib.parse import parse_qsl

import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import srgssr
from menus import MenuBuilder
import utils
import re

ADDON_ID = "plugin.video.rtsplaytv"
REAL_SETTINGS = xbmcaddon.Addon(id=ADDON_ID)
ADDON_NAME = REAL_SETTINGS.getAddonInfo("name")
ADDON_VERSION = REAL_SETTINGS.getAddonInfo("version")
DEBUG = REAL_SETTINGS.getSetting("Enable_Debugging") == "true"
CONTENT_TYPE = "videos"


class RTSMenuBuilder(MenuBuilder):
    """RTS-specific MenuBuilder that dynamically adds and removes context menu items."""

    def __init__(self, srgssr_instance):
        MenuBuilder.__init__(self, srgssr_instance)
        self.play_later_urns = None
        self.continue_watching_urns = None

    def _load_user_lists(self):
        if self.play_later_urns is not None:
            return

        self.play_later_urns = set()
        self.continue_watching_urns = set()

        from resources.lib.auth import RTSAuth
        auth = RTSAuth(self.srgssr.real_settings)
        cookies = auth.get_cookies()
        if not cookies:
            return

        import requests
        import time
        # 1. Fetch Play Later Bookmarks
        try:
            cb = int(time.time() * 1000)
            res = requests.get(f"https://profil.rts.ch/api/playlist/v3/watch_later?cb={cb}", cookies=cookies, timeout=5)
            if res.ok:
                bookmarks = res.json().get("bookmarks") or []
                for b in bookmarks:
                    urn = b.get("itemId") or b.get("item_id")
                    if urn:
                        self.play_later_urns.add(urn)
        except Exception:
            pass

        # 2. Fetch Continue Watching History
        try:
            cb = int(time.time() * 1000)
            res = requests.get(f"https://profil.rts.ch/api/history/v2?cb={cb}", cookies=cookies, timeout=5)
            if res.ok:
                data = res.json()
                items = data if isinstance(data, list) else (data.get("items") or data.get("history") or [])
                for item in items:
                    if item.get("deleted") is True:
                        continue
                    urn = item.get("item_id")
                    if urn:
                        raw_id = urn.split(":")[-1] if ":" in urn else urn
                        self.continue_watching_urns.add(raw_id)
        except Exception:
            pass

    def build_entry_apiv3(self, data, is_show=False, whitelist_ids=None):
        urn = data["urn"]
        self.srgssr.log(f"RTSMenuBuilder.build_entry_apiv3: urn = {urn}")
        title = utils.try_get(data, "title")

        if utils.try_get(data, "type") == "SCHEDULED_LIVESTREAM":
            dt = utils.try_get(data, "date")
            if dt:
                dt = utils.parse_datetime(dt)
            if dt:
                dts = dt.strftime("(%d.%m.%Y, %H:%M)")
                title = dts + " " + title

        media_id = utils.try_get(data, "id")
        if whitelist_ids is not None and media_id not in whitelist_ids:
            return
        description = utils.try_get(data, "description")
        lead = utils.try_get(data, "lead")
        image_url = utils.try_get(data, "imageUrl")
        poster_image_url = utils.try_get(data, "posterImageUrl")
        show_image_url = utils.try_get(data, ["show", "imageUrl"])
        show_poster_image_url = utils.try_get(data, ["show", "posterImageUrl"])
        duration = utils.try_get(data, "duration", int, default=None)
        if duration:
            duration //= 1000
        date = utils.try_get(data, "date")
        kodi_date_string = date
        dto = utils.parse_datetime(date)
        kodi_date_string = dto.strftime("%Y-%m-%d") if dto else None
        label = title or urn
        list_item = xbmcgui.ListItem(label=label)
        list_item.setInfo(
            "video",
            {
                "title": title,
                "plot": description or lead,
                "plotoutline": lead or description,
                "duration": duration,
                "aired": kodi_date_string,
            },
        )
        if is_show:
            poster = (
                show_poster_image_url
                or poster_image_url
                or show_image_url
                or image_url
            )
        else:
            poster = (
                image_url
                or poster_image_url
                or show_poster_image_url
                or show_image_url
            )
        list_item.setArt(
            {
                "thumb": image_url,
                "poster": poster,
                "fanart": show_image_url or self.srgssr.fanart,
                "banner": show_image_url or image_url,
            }
        )
        url = self.srgssr.build_url(mode=100, name=urn)
        is_folder = True

        # Inject context menus conditionally
        if "video" in urn:
            self._load_user_lists()
            context_items = []
            from resources.lib.auth import RTSAuth
            if RTSAuth(self.srgssr.real_settings).get_cookies():
                if urn in self.play_later_urns:
                    remove_url = self.srgssr.build_url(mode="remove_from_play_later", name=urn)
                    context_items.append(("Remove from Play Later", f"RunPlugin('{remove_url}')"))
                else:
                    add_url = self.srgssr.build_url(mode="add_to_play_later", name=urn)
                    context_items.append(("Add to Play Later", f"RunPlugin('{add_url}')"))

                raw_id = urn.split(":")[-1] if ":" in urn else urn
                if raw_id in self.continue_watching_urns:
                    clear_url = self.srgssr.build_url(mode="remove_from_continue_watching", name=urn)
                    context_items.append(("Remove from Continue Watching", f"RunPlugin('{clear_url}')"))

            if context_items:
                list_item.addContextMenuItems(context_items)

        xbmcplugin.addDirectoryItem(
            self.handle, url, list_item, isFolder=is_folder
        )

    def build_entry(
        self,
        json_entry,
        is_folder=False,
        fanart=None,
        urn=None,
        show_image_url=None,
        show_poster_image_url=None,
    ):
        self.srgssr.log("RTSMenuBuilder.build_entry")
        title = utils.try_get(json_entry, "title")
        vid = utils.try_get(json_entry, "id")
        description = utils.try_get(json_entry, "description")
        lead = utils.try_get(json_entry, "lead")
        image_url = utils.try_get(json_entry, "imageUrl")
        poster_image_url = utils.try_get(json_entry, "posterImageUrl")
        if not urn:
            urn = utils.try_get(json_entry, "urn")

        if image_url:
            image_url = re.sub(r"/\d+x\d+", "", image_url)

        duration = utils.try_get(
            json_entry, "duration", data_type=int, default=None
        )
        if duration:
            duration = duration // 1000
        else:
            duration = utils.get_duration(
                utils.try_get(json_entry, "duration")
            )

        date_string = utils.try_get(json_entry, "date")
        dto = utils.parse_datetime(date_string)
        kodi_date_string = dto.strftime("%Y-%m-%d") if dto else None

        list_item = xbmcgui.ListItem(label=title)
        list_item.setInfo(
            "video",
            {
                "title": title,
                "plot": description or lead,
                "plotoutline": lead,
                "duration": duration,
                "aired": kodi_date_string,
            },
        )

        if not fanart:
            fanart = image_url

        poster = (
            image_url
            or poster_image_url
            or show_poster_image_url
            or show_image_url
        )
        list_item.setArt(
            {
                "thumb": image_url,
                "poster": poster,
                "fanart": show_image_url or fanart,
                "banner": show_image_url or image_url,
            }
        )

        subs = utils.try_get(
            json_entry, "subtitleList", data_type=list, default=[]
        )
        if subs:
            subtitle_list = [
                utils.try_get(x, "url")
                for x in subs
                if utils.try_get(x, "format") == "VTT"
            ]
            if subtitle_list:
                list_item.setSubtitles(subtitle_list)
            else:
                self.srgssr.log(
                    f"No WEBVTT subtitles found for video id {vid}."
                )

        name = vid

        if is_folder:
            list_item.setProperty("IsPlayable", "false")
            url = self.srgssr.build_url(mode=21, name=name)
        else:
            list_item.setProperty("IsPlayable", "true")
            if urn and "swisstxt" in urn:
                url = self.srgssr.build_url(mode=50, name=urn)
            else:
                url = self.srgssr.build_url(mode=50, name=name)

        if not is_folder:
            context_urn = urn if urn else f"urn:rts:video:{name}"
            self._load_user_lists()
            context_items = []
            from resources.lib.auth import RTSAuth
            if RTSAuth(self.srgssr.real_settings).get_cookies():
                if context_urn in self.play_later_urns:
                    remove_url = self.srgssr.build_url(mode="remove_from_play_later", name=context_urn)
                    context_items.append(("Remove from Play Later", f"RunPlugin('{remove_url}')"))
                else:
                    add_url = self.srgssr.build_url(mode="add_to_play_later", name=context_urn)
                    context_items.append(("Add to Play Later", f"RunPlugin('{add_url}')"))

                raw_id = context_urn.split(":")[-1] if ":" in context_urn else context_urn
                if raw_id in self.continue_watching_urns:
                    clear_url = self.srgssr.build_url(mode="remove_from_continue_watching", name=context_urn)
                    context_items.append(("Remove from Continue Watching", f"RunPlugin('{clear_url}')"))

            if context_items:
                list_item.addContextMenuItems(context_items)

        xbmcplugin.addDirectoryItem(
            self.handle, url, list_item, isFolder=is_folder
        )


class RTSPlayTV(srgssr.SRGSSR):
    def __init__(self):
        super(RTSPlayTV, self).__init__(
            int(sys.argv[1]), bu="rts", addon_id=ADDON_ID
        )
        self.menu_builder = RTSMenuBuilder(self)

    def build_livetv_menu(self, sub_menu=None):
        """Fetches 24/7 channels and scheduled event livestreams.

        If sub_menu is None, renders 24/7 channels and folder links for
        sub-menus.
        If sub_menu is "sports" or "others", renders only that category of
        events.
        """
        import json
        import datetime

        headers = {'User-Agent': 'Mozilla/5.0'}

        # Case 1: Build the main root "Direct TV" page
        if sub_menu is None:
            # 1. Fetch available 24/7 livestreams
            livestreams_url = (
                "https://www.rts.ch/play/v3/api/rts/production/tv-livestreams"
            )
            try:
                req = urllib.request.Request(livestreams_url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    livestreams_data = json.loads(
                        response.read().decode('utf-8')
                    )
            except Exception as e:
                log(f"Failed to fetch live TV channels: {e}", xbmc.LOGERROR)
                return

            channels = livestreams_data.get("data", [])
            if not channels:
                return

            # 2. Fetch program guide for EPG data (enriched fallback)
            guide_url = (
                "https://www.rts.ch/play/v3/api/rts/production/"
                "tv-program-guide"
            )
            guide_by_channel = {}
            try:
                req = urllib.request.Request(guide_url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    guide_data = json.loads(response.read().decode('utf-8'))
                    for item in guide_data.get("data", []):
                        ch_id = item.get("channel", {}).get("id")
                        if ch_id:
                            guide_by_channel[ch_id] = item.get(
                                "programList", []
                            )
            except Exception as e:
                log(
                    "Failed to fetch live TV program guide "
                    f"(falling back to channels-only): {e}",
                    xbmc.LOGWARNING,
                )

            now_utc = datetime.datetime.now(datetime.timezone.utc)

            # Add 24/7 linear channels
            for channel in channels:
                title = channel.get("title")
                urn = channel.get("livestreamUrn")
                img_url = channel.get("imageUrl")
                channel_id = channel.get("channelId")

                if title and urn:
                    current_show = ""
                    next_show = ""

                    program_list = guide_by_channel.get(channel_id, [])
                    for prog in program_list:
                        try:
                            start = datetime.datetime.fromisoformat(
                                prog["startTime"].replace('Z', '+00:00')
                            )
                            end = datetime.datetime.fromisoformat(
                                prog["endTime"].replace('Z', '+00:00')
                            )
                            if start <= now_utc <= end:
                                current_show = prog["title"]
                                break
                            elif start > now_utc:
                                if not next_show:
                                    formatted_start = (
                                        start.astimezone().strftime("%H:%M")
                                    )
                                    next_show = (
                                        "À suivre : "
                                        f"{prog['title']} ({formatted_start})"
                                    )
                        except Exception:
                            pass

                    status = current_show if current_show else next_show
                    display_name = f"{title} - {status}" if status else title

                    list_item = xbmcgui.ListItem(label=display_name)
                    list_item.setProperty("IsPlayable", "true")
                    if img_url:
                        list_item.setArt({"thumb": img_url})

                    plugin_url = self.build_url(mode=50, name=urn)
                    xbmcplugin.addDirectoryItem(
                        self.handle, plugin_url, list_item, isFolder=False
                    )

            # Add folder items for the two sub-directories
            sport_folder_url = self.build_url(mode=90, name="sports")
            sport_item = xbmcgui.ListItem(
                label=self.language(30101)
                or self.plugin_language(30101)
                or "Sports Live"
            )
            sport_item.setArt({"icon": self.icon})
            xbmcplugin.addDirectoryItem(
                self.handle, sport_folder_url, sport_item, isFolder=True
            )

            others_folder_url = self.build_url(mode=90, name="others")
            others_item = xbmcgui.ListItem(
                label=self.language(30102)
                or self.plugin_language(30102)
                or "Other Live Streams"
            )
            others_item.setArt({"icon": self.icon})
            xbmcplugin.addDirectoryItem(
                self.handle, others_folder_url, others_item, isFolder=True
            )

        # Case 2: Build a specific subdirectory ("sports" or "others") and
        # its date folders/events
        else:
            sub_category, sep, date_filter = sub_menu.partition('_')

            # Fetch scheduled event livestreams with caching
            scheduled_url = (
                "https://il.srgssr.ch/integrationlayer/2.0/rts/"
                "mediaList/video/"
                "scheduledLivestreams?vector=portalplay&pageSize=100"
            )
            scheduled_events = []
            try:
                # Use SRGSSR's caching open_url to avoid redundant requests
                response_text = self.open_url(scheduled_url)
                if response_text:
                    scheduled_data = json.loads(response_text)
                    scheduled_events = (
                        scheduled_data.get("mediaList")
                        or scheduled_data.get("data")
                        or []
                    )
            except Exception as e:
                log(
                    f"Failed to fetch scheduled livestreams: {e}",
                    xbmc.LOGWARNING,
                )

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            local_now = now_utc.astimezone()

            # Filter out past events
            active_and_upcoming = []
            for item in scheduled_events:
                try:
                    valid_to = datetime.datetime.fromisoformat(
                        item["validTo"].replace('Z', '+00:00')
                    )
                    if valid_to >= now_utc:
                        active_and_upcoming.append(item)
                except Exception:
                    pass

            # Filter by sub-category using the Integration Layer's
            # "creatorUser" metadata attribute
            filtered_events = []
            for item in active_and_upcoming:
                is_sport = item.get("creatorUser") == "MMSport"

                if (sub_category == "sports" and is_sport) or (
                    sub_category == "others" and not is_sport
                ):
                    filtered_events.append(item)

            # Group filtered events by local date
            grouped_events = {}
            for item in filtered_events:
                try:
                    valid_from = datetime.datetime.fromisoformat(
                        item["validFrom"].replace('Z', '+00:00')
                    )
                    local_start = valid_from.astimezone()
                    local_date = local_start.date()
                    if local_date not in grouped_events:
                        grouped_events[local_date] = []
                    grouped_events[local_date].append((local_start, item))
                except Exception:
                    pass

            sorted_dates = sorted(grouped_events.keys())

            def folder_name(dato):
                weekdays = (
                    self.language(30060),  # Monday
                    self.language(30061),  # Tuesday
                    self.language(30062),  # Wednesday
                    self.language(30063),  # Thursday
                    self.language(30064),  # Friday
                    self.language(30065),  # Saturday
                    self.language(30066),  # Sunday
                )
                today = local_now.date()
                if dato == today:
                    return self.language(30058)  # Today
                elif dato == today - datetime.timedelta(days=1):
                    return self.language(30059)  # Yesterday
                return "%s, %s" % (
                    weekdays[dato.weekday()],
                    dato.strftime("%d.%m.%Y"),
                )

            if not date_filter:
                # Build Date Folders
                for local_date in sorted_dates:
                    folder_label = folder_name(local_date)
                    folder_item = xbmcgui.ListItem(label=folder_label)
                    folder_item.setArt({"icon": self.icon})

                    target_name = f"{sub_category}_{local_date.isoformat()}"
                    folder_url = self.build_url(mode=90, name=target_name)
                    xbmcplugin.addDirectoryItem(
                        self.handle, folder_url, folder_item, isFolder=True
                    )
            else:
                # Render events chronologically for the selected date
                selected_date = datetime.date.fromisoformat(date_filter)
                events_on_day = grouped_events.get(selected_date, [])
                events_on_day.sort(key=lambda x: x[0])

                for start_time, item in events_on_day:
                    title = item.get("title")
                    urn = item.get("urn")
                    img_url = item.get("imageUrl")

                    # cesimId, when present, is the real swisstxt asset id
                    # and takes priority over the event's own uuid (which
                    # doesn't resolve on its own for these events).
                    cesim_id = item.get("cesimId")
                    if cesim_id:
                        urn = f"urn:swisstxt:video:rts:{cesim_id}"

                    if title and urn:
                        try:
                            valid_to = datetime.datetime.fromisoformat(
                                item["validTo"].replace('Z', '+00:00')
                            )
                            if start_time <= now_utc <= valid_to:
                                prefix = "[COLOR red][LIVE] [/COLOR]"
                            else:
                                prefix = f"[{start_time.strftime('%H:%M')}] "
                        except Exception:
                            prefix = ""

                        display_name = f"{prefix}{title}"
                        list_item = xbmcgui.ListItem(label=display_name)
                        list_item.setProperty("IsPlayable", "true")
                        if img_url:
                            list_item.setArt({"thumb": img_url})

                        plugin_url = self.build_url(
                            mode=50, name=urn, title=title
                        )
                        xbmcplugin.addDirectoryItem(
                            self.handle, plugin_url, list_item, isFolder=False
                        )


def log(msg, level=xbmc.LOGDEBUG):
    """
    Logs a message using Kodi's logging interface.

    Keyword arguments:
    msg   -- the message to log
    level -- the logging level
    """
    if DEBUG:
        if level == xbmc.LOGERROR:
            msg += " ," + traceback.format_exc()
    xbmc.log(ADDON_ID + "-" + ADDON_VERSION + "-" + msg, level)


def get_params():
    if len(sys.argv) >= 3 and sys.argv[2]:
        query_string = sys.argv[2][1:]
    else:
        from urllib.parse import urlparse
        query_string = urlparse(sys.argv[0]).query
    return dict(parse_qsl(query_string))


def run():
    """
    Run the plugin.
    """
    params = get_params()
    try:
        url = unquote_plus(params["url"])
    except Exception:
        url = None
    try:
        name = unquote_plus(params["name"])
    except Exception:
        name = None
    try:
        mode_val = params.get("mode")
        if mode_val is not None and mode_val.isdigit():
            mode = int(mode_val)
        else:
            mode = mode_val
    except Exception:
        mode = None
    try:
        page_hash = unquote_plus(params["page_hash"])
    except Exception:
        page_hash = None
    try:
        page = unquote_plus(params["page"])
    except Exception:
        page = None
    try:
        title = unquote_plus(params["title"])
    except Exception:
        title = None

    log("Mode: " + str(mode))
    log("URL : " + str(url))
    log("Name: " + str(name))
    log("Page Hash: " + str(page_hash))
    log("Page: " + str(page))

    if mode is None:
        identifiers = [
            "All_Shows",
            "Favourite_Shows",
            "Newest_Favourite_Shows",
            "Homepage",
            "Topics",
            "Shows_By_Date",
            "Search",
            "RTS_YouTube",
        ]
        rts = RTSPlayTV()
        rts.menu_builder.build_main_menu(identifiers)

        # Append Direct TV to the main menu
        tv_list_item = xbmcgui.ListItem(label="Direct TV")
        tv_list_item.setArt({"icon": rts.icon})
        tv_url = rts.build_url(mode=90)
        xbmcplugin.addDirectoryItem(
            int(sys.argv[1]), tv_url, tv_list_item, isFolder=True
        )

        # Append Continue Watching if authenticated
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        if auth.get_cookies():
            cw_list_item = xbmcgui.ListItem(label="Continue Watching")
            cw_list_item.setArt({"icon": rts.icon})
            cw_url = rts.build_url(mode="continue_watching")
            xbmcplugin.addDirectoryItem(
                int(sys.argv[1]), cw_url, cw_list_item, isFolder=True
            )

            pl_list_item = xbmcgui.ListItem(label="Play Later")
            pl_list_item.setArt({"icon": rts.icon})
            pl_url = rts.build_url(mode="play_later")
            xbmcplugin.addDirectoryItem(
                int(sys.argv[1]), pl_url, pl_list_item, isFolder=True
            )
    elif mode == 10:
        RTSPlayTV().menu_builder.build_all_shows_menu()
    elif mode == 11:
        RTSPlayTV().menu_builder.build_favourite_shows_menu()
    elif mode == 12:
        RTSPlayTV().menu_builder.build_newest_favourite_menu(page=page)
    elif mode == 13:
        RTSPlayTV().menu_builder.build_topics_menu()
    elif mode == 17:
        RTSPlayTV().menu_builder.build_dates_overview_menu()
    elif mode == 19:
        RTSPlayTV().manage_favourite_shows()
    elif mode == 21:
        RTSPlayTV().menu_builder.build_episode_menu(name)
    elif mode == 24:
        RTSPlayTV().menu_builder.build_date_menu(name)
    elif mode == 60:
        RTSPlayTV().menu_builder.build_specific_date_menu(name)
    elif mode == 25:
        RTSPlayTV().menu_builder.pick_date()
    elif mode == 27:
        RTSPlayTV().menu_builder.build_search_menu()
    elif mode == 28:
        RTSPlayTV().menu_builder.build_search_media_menu(
            mode=mode, name=name, page=page, page_hash=page_hash
        )
    elif mode == 70:
        RTSPlayTV().menu_builder.build_recent_search_menu()
    elif mode == 30:
        RTSPlayTV().youtube_builder.build_youtube_channel_overview_menu(33)
    elif mode == 33:
        RTSPlayTV().youtube_builder.build_youtube_channel_menu(
            name, mode, page=page, page_token=page_hash
        )
    elif mode == 50:
        import os
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies_path = auth.session_file
        addon_path = rts.real_settings.getAddonInfo("path")
        monitor_script = os.path.join(addon_path, "resources", "lib", "monitor.py")
        escaped_name = name.replace('"', '\\"').replace("'", "\\'") if name else ""
        escaped_title = title.replace('"', '\\"').replace("'", "\\'") if title else "Video"
        escaped_cookies_path = cookies_path.replace('"', '\\"').replace("'", "\\'") if cookies_path else ""
        xbmc.executebuiltin(f'RunScript("{monitor_script}", "{escaped_name}", "{escaped_title}", "{escaped_cookies_path}")')
        rts.player.play_video(name, title=title)
    elif mode == "continue_watching":
        import time
        import requests
        import json
        import re
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies = auth.get_cookies()
        if cookies:
            url = "https://profil.rts.ch/api/history/v2"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.rts.ch/"
            }
            try:
                cb = int(time.time() * 1000)
                res = requests.get(f"{url}?cb={cb}", headers=headers, cookies=cookies, timeout=10)
                if res.ok:
                    data = res.json()
                    items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("items") or data.get("history") or data.get("data") or []
                    seen_urns = set()
                    count = 0
                    for item in items:
                        if count >= 30:
                            break
                        if item.get("deleted") is True:
                            continue
                        urn = item.get("item_id")
                        resume_seconds = item.get("last_playback_position")
                        # Strictly require full URN format (containing a colon ':') to filter out old raw-ID runs
                        if not urn or ":" not in urn or resume_seconds is None:
                            continue

                        # Deduplicate: normalize full URNs to their raw UUID hash for perfect deduplication
                        raw_id = urn.split(":")[-1] if ":" in urn else urn
                        if raw_id in seen_urns:
                            continue
                        seen_urns.add(raw_id)

                        # Fetch the metadata from the Integration Layer (guaranteed to contain colon)
                        json_url = f"https://il.srgssr.ch/integrationlayer/2.0/mediaComposition/byUrn/{urn}.json"

                        try:
                            content = rts.open_url(json_url, use_cache=True, notify_on_error=False)
                            if not content:
                                continue
                            json_response = json.loads(content)

                            chapter_urn = json_response.get("chapterUrn")
                            chapter_id = chapter_urn.split(":")[-1] if chapter_urn else None

                            json_chapter_list = json_response.get("chapterList") or []
                            json_chapter = None
                            for chapter in json_chapter_list:
                                if chapter.get("id") == chapter_id:
                                    json_chapter = chapter
                                    break

                            if not json_chapter:
                                continue

                            title = json_chapter.get("title") or "Video"
                            description = json_chapter.get("description") or json_chapter.get("lead")
                            image_url = json_chapter.get("imageUrl")
                            if image_url:
                                image_url = re.sub(r"/\d+x\d+", "", image_url)

                            duration_ms = json_chapter.get("duration")
                            duration_sec = int(duration_ms // 1000) if isinstance(duration_ms, (int, float)) else 0

                            # Filter out live streams / zero duration items
                            if duration_sec <= 0:
                                continue

                            # Filter out accidental plays (less than 60 seconds watched)
                            if resume_seconds < 60:
                                continue

                            # Filter out completed videos (more than 90% watched)
                            if resume_seconds >= duration_sec * 0.9:
                                continue

                            # Create ListItem
                            list_item = xbmcgui.ListItem(label=title)
                            list_item.setInfo(
                                "video",
                                {
                                    "title": title,
                                    "plot": description,
                                    "duration": duration_sec,
                                },
                            )
                            if image_url:
                                list_item.setArt({"thumb": image_url, "poster": image_url, "fanart": image_url})

                            # Set play progress / resume position for Kodi to display progress and prompt for resume
                            if resume_seconds > 0 and duration_sec > 0:
                                list_item.setProperty("ResumeTime", str(int(resume_seconds)))
                                list_item.setProperty("TotalTime", str(int(duration_sec)))

                            list_item.setProperty("inputstream", "inputstream.adaptive")
                            list_item.setProperty("IsPlayable", "true")

                            # Build Context Menu for Continue Watching item
                            cw_url = rts.build_url(mode="remove_from_continue_watching", name=urn)
                            pl_url = rts.build_url(mode="add_to_play_later", name=urn)
                            list_item.addContextMenuItems([
                                ("Add to Play Later", f"RunPlugin('{pl_url}')"),
                                ("Remove from Continue Watching", f"RunPlugin('{cw_url}')")
                            ])

                            # Build the playable URL (mode=50 is the player, passing name=urn and title=title)
                            play_url = rts.build_url(mode=50, name=urn, title=title)

                            xbmcplugin.addDirectoryItem(int(sys.argv[1]), play_url, list_item, isFolder=False)
                            count += 1
                        except Exception as inner_e:
                            log(f"Failed to process history item {urn}: {inner_e}", xbmc.LOGDEBUG)
            except Exception as e:
                log(f"Failed to build Continue Watching menu: {e}", xbmc.LOGERROR)
    elif mode == 100:
        RTSPlayTV().menu_builder.build_menu_by_urn(name)
    elif mode == 200:
        RTSPlayTV().menu_builder.build_homepage_menu()
    elif mode == 90:
        RTSPlayTV().build_livetv_menu(name)
    elif mode == 1000:
        RTSPlayTV().menu_builder.build_menu_apiv3(name, mode, page, page_hash)
    elif mode == "login":
        import os
        from resources.lib.auth import RTSAuth
        rts = RTSPlayTV()
        auth = RTSAuth(rts.real_settings)
        if os.path.exists(auth.session_file):
            try:
                os.remove(auth.session_file)
            except Exception:
                pass
        try:
            if auth.prompt_credentials_and_login():
                xbmcgui.Dialog().ok(
                    ADDON_NAME,
                    rts.plugin_language(30083) or "Login successful."
                )
        except Exception as e:
            xbmcgui.Dialog().ok(
                ADDON_NAME,
                f"{rts.plugin_language(30084) or 'Login failed.'}\nError: {e}"
            )
    elif mode == "play_later":
        import time
        import requests
        import json
        import re
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies = auth.get_cookies()
        if cookies:
            url = "https://profil.rts.ch/api/playlist/v3/watch_later"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.rts.ch/"
            }
            try:
                cb = int(time.time() * 1000)
                res = requests.get(f"{url}?cb={cb}", headers=headers, cookies=cookies, timeout=10)
                if res.ok:
                    data = res.json()
                    # PEACH Watch Later returns list under "bookmarks" key
                    items = data.get("bookmarks") or data.get("items") or data.get("history") or data.get("data") or []
                    seen_urns = set()
                    count = 0
                    for item in items:
                        if count >= 50:
                            break
                        # PEACH Watch Later uses camelCase "itemId"
                        urn = item.get("itemId") or item.get("item_id")
                        if not urn:
                            continue

                        # Deduplicate: only process each unique URN once
                        if urn in seen_urns:
                            continue
                        seen_urns.add(urn)

                        # Fetch the metadata from the Integration Layer (guaranteed to contain colon)
                        json_url = f"https://il.srgssr.ch/integrationlayer/2.0/mediaComposition/byUrn/{urn}.json"

                        try:
                            content = rts.open_url(json_url, use_cache=True, notify_on_error=False)
                            if not content:
                                continue
                            json_response = json.loads(content)

                            chapter_urn = json_response.get("chapterUrn")
                            chapter_id = chapter_urn.split(":")[-1] if chapter_urn else None

                            json_chapter_list = json_response.get("chapterList") or []
                            json_chapter = None
                            for chapter in json_chapter_list:
                                if chapter.get("id") == chapter_id:
                                    json_chapter = chapter
                                    break

                            if not json_chapter:
                                continue

                            title = json_chapter.get("title") or "Video"
                            description = json_chapter.get("description") or json_chapter.get("lead")
                            image_url = json_chapter.get("imageUrl")
                            if image_url:
                                image_url = re.sub(r"/\d+x\d+", "", image_url)

                            duration_ms = json_chapter.get("duration")
                            duration_sec = int(duration_ms // 1000) if isinstance(duration_ms, (int, float)) else 0

                            # Create ListItem
                            list_item = xbmcgui.ListItem(label=title)
                            list_item.setInfo(
                                "video",
                                {
                                    "title": title,
                                    "plot": description,
                                    "duration": duration_sec,
                                },
                            )
                            if image_url:
                                list_item.setArt({"thumb": image_url, "poster": image_url, "fanart": image_url})

                            list_item.setProperty("inputstream", "inputstream.adaptive")
                            list_item.setProperty("IsPlayable", "true")

                            # Build Context Menu for Play Later item
                            remove_url = rts.build_url(mode="remove_from_play_later", name=urn)
                            list_item.addContextMenuItems([
                                ("Remove from Play Later", f"RunPlugin('{remove_url}')")
                            ])

                            play_url = rts.build_url(mode=50, name=urn, title=title)
                            xbmcplugin.addDirectoryItem(int(sys.argv[1]), play_url, list_item, isFolder=False)
                            count += 1
                        except Exception as inner_e:
                            log(f"Failed to process play later item {urn}: {inner_e}", xbmc.LOGDEBUG)
            except Exception as e:
                log(f"Failed to build Play Later menu: {e}", xbmc.LOGERROR)
    elif mode == "add_to_play_later":
        import requests
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies = auth.get_cookies()
        if cookies:
            urn_val = name
            if ":" not in urn_val:
                urn_val = f"urn:rts:video:{urn_val}"
            url = "https://profil.rts.ch/api/playlist/v3/watch_later/bookmarks"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Referer": "https://www.rts.ch/"
            }
            try:
                payload = {"itemId": urn_val}
                res = requests.post(url, json=payload, headers=headers, cookies=cookies, timeout=10)
                if res.ok or res.status_code == 201:
                    xbmcgui.Dialog().notification("RTS Play TV", "Added to Play Later", rts.icon, 3000)
                    xbmc.executebuiltin("Container.Refresh")
                else:
                    xbmcgui.Dialog().notification("RTS Play TV", "Failed to add", rts.icon, 3000)
            except Exception as e:
                log(f"Failed to add to Play Later: {e}", xbmc.LOGERROR)
    elif mode == "remove_from_play_later":
        import requests
        import time
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies = auth.get_cookies()
        if cookies:
            urn_val = name
            if ":" not in urn_val:
                urn_val = f"urn:rts:video:{urn_val}"
            url_get = "https://profil.rts.ch/api/playlist/v3/watch_later"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.rts.ch/"
            }
            try:
                cb = int(time.time() * 1000)
                res_get = requests.get(f"{url_get}?cb={cb}", headers=headers, cookies=cookies, timeout=10)
                bookmark_id = None
                if res_get.ok:
                    data = res_get.json()
                    bookmarks = data.get("bookmarks") or []
                    for b in bookmarks:
                        if b.get("itemId") == urn_val or b.get("item_id") == urn_val:
                            bookmark_id = b.get("id")
                            break

                if bookmark_id is not None:
                    url_delete = f"https://profil.rts.ch/api/playlist/v3/watch_later/bookmarks/{bookmark_id}"
                    res_del = requests.delete(url_delete, headers=headers, cookies=cookies, timeout=10)
                    if res_del.ok or res_del.status_code == 204:
                        xbmcgui.Dialog().notification("RTS Play TV", "Removed from Play Later", rts.icon, 3000)
                        xbmc.executebuiltin("Container.Refresh")
                    else:
                        xbmcgui.Dialog().notification("RTS Play TV", "Failed to remove", rts.icon, 3000)
                else:
                    xbmcgui.Dialog().notification("RTS Play TV", "Removed from Play Later", rts.icon, 3000)
                    xbmc.executebuiltin("Container.Refresh")
            except Exception as e:
                log(f"Failed to remove from Play Later: {e}", xbmc.LOGERROR)
    elif mode == "remove_from_continue_watching":
        import requests
        import time
        rts = RTSPlayTV()
        from resources.lib.auth import RTSAuth
        auth = RTSAuth(rts.real_settings)
        cookies = auth.get_cookies()
        if cookies:
            urn_val = name
            if ":" not in urn_val:
                urn_val = f"urn:rts:video:{urn_val}"
            url = "https://profil.rts.ch/api/history/v2"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Referer": "https://www.rts.ch/"
            }
            payload = {
                "item_id": urn_val,
                "deleted": True,
                "date": int(time.time() * 1000)
            }
            try:
                res = requests.post(url, json=payload, headers=headers, cookies=cookies, timeout=10)
                if res.ok:
                    xbmcgui.Dialog().notification("RTS Play TV", "Removed from Continue Watching", rts.icon, 3000)
                    xbmc.executebuiltin("Container.Refresh")
                else:
                    xbmcgui.Dialog().notification("RTS Play TV", "Failed to remove", rts.icon, 3000)
            except Exception as e:
                log(f"Failed to remove from Continue Watching: {e}", xbmc.LOGERROR)

    handle = int(sys.argv[1])
    if handle >= 0:
        xbmcplugin.setContent(handle, CONTENT_TYPE)
        xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
        xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_NONE)
        xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_LABEL)
        xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_TITLE)
        xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
