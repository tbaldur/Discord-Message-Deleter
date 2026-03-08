import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import requests

# When running as a PyInstaller exe, sys.executable is the exe path.
# When running as a script, __file__ is the script path.
_BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
PACKAGE_PATH = _BASE_DIR / "package"
DELETED_FILE = _BASE_DIR / "deleted.json"
DISCOVERED_FILE = _BASE_DIR / "discovered.json"
API_BASE = "https://discord.com/api/v9"
DELETE_DELAY = 1.4  # seconds between deletes


def load_deleted():
    """Load set of already-deleted message IDs from tracking file."""
    if DELETED_FILE.exists():
        with open(DELETED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_deleted(deleted_ids):
    """Save set of deleted message IDs to tracking file."""
    with open(DELETED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(deleted_ids), f)


def load_discovered():
    """Load discovered channels/messages. Returns dict: {channel_id: {display_name, category, message_ids[]}}."""
    if DISCOVERED_FILE.exists():
        with open(DISCOVERED_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_discovered(data):
    """Save discovered channels/messages."""
    with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_channels():
    """Load all channels from the local Discord data package."""
    messages_dir = PACKAGE_PATH / "Messages"
    deleted_ids = load_deleted()

    # Load display name index
    with open(messages_dir / "index.json", encoding="utf-8") as f:
        index = json.load(f)

    channels = []
    for folder in messages_dir.iterdir():
        if not folder.is_dir() or not folder.name.startswith("c"):
            continue

        channel_file = folder / "channel.json"
        messages_file = folder / "messages.json"
        if not channel_file.exists() or not messages_file.exists():
            continue

        with open(channel_file, encoding="utf-8") as f:
            chan = json.load(f)
        with open(messages_file, encoding="utf-8") as f:
            msgs = json.load(f)

        if not msgs:
            continue

        cid = chan["id"]
        ctype = chan.get("type", "")
        raw_name = index.get(cid, "Unknown channel")

        # Determine category and display name
        if ctype == "DM":
            category = "Direct Messages"
            display_name = raw_name.replace("Direct Message with ", "") if raw_name.startswith("Direct Message with ") else raw_name
        elif ctype.startswith("GUILD_"):
            guild = chan.get("guild", {})
            category = guild.get("name", "Unknown Server")
            # index format: "channel in Server" — extract just channel name
            if " in " in raw_name:
                display_name = raw_name.rsplit(" in ", 1)[0]
            else:
                display_name = chan.get("name", raw_name)
        elif ctype == "GROUP_DM":
            category = "Group DMs"
            display_name = raw_name if raw_name and raw_name != "None" else f"Group ({cid})"
        else:
            category = "Other"
            display_name = raw_name

        message_ids = [str(m["ID"]) for m in msgs if str(m["ID"]) not in deleted_ids]
        channels.append({
            "id": cid,
            "type": ctype,
            "display_name": display_name,
            "category": category,
            "message_ids": message_ids,
            "message_count": len(message_ids),
        })

    # Merge in discovered channels/messages
    discovered = load_discovered()
    package_channel_ids = {ch["id"] for ch in channels}
    package_msg_lookup = {ch["id"]: set(ch["message_ids"]) for ch in channels}

    for cid, dch in discovered.items():
        disc_mids = [mid for mid in dch["message_ids"] if mid not in deleted_ids]
        if cid in package_channel_ids:
            # Add new message IDs to existing channel
            for ch in channels:
                if ch["id"] == cid:
                    existing = set(ch["message_ids"])
                    new_mids = [mid for mid in disc_mids if mid not in existing]
                    if new_mids:
                        ch["message_ids"].extend(new_mids)
                        ch["message_count"] = len(ch["message_ids"])
                    break
        elif disc_mids:
            # Brand new channel not in package
            channels.append({
                "id": cid,
                "type": dch.get("type", ""),
                "display_name": dch["display_name"],
                "category": dch["category"],
                "message_ids": disc_mids,
                "message_count": len(disc_mids),
            })

    # Remove channels with 0 messages after filtering
    channels = [ch for ch in channels if ch["message_count"] > 0]
    channels.sort(key=lambda c: (c["category"], c["display_name"].lower()))
    return channels


class DiscordDeleterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Discord Message Deleter")
        self.root.geometry("620x700")
        self.root.minsize(500, 500)

        self.is_running = False
        self.channel_vars = {}  # cid -> BooleanVar
        self.channels = []

        self._build_ui()
        self._load_channels()

    def _build_ui(self):
        # Token frame
        token_frame = ttk.LabelFrame(self.root, text="Auth Token", padding=8)
        token_frame.pack(fill="x", padx=10, pady=(10, 5))

        self.token_var = tk.StringVar()
        self.token_entry = ttk.Entry(token_frame, textvariable=self.token_var, show="*", width=60)
        self.token_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.show_token = False
        self.toggle_btn = ttk.Button(token_frame, text="Show", width=6, command=self._toggle_token)
        self.toggle_btn.pack(side="right")

        # Buttons frame
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(btn_frame, text="Select All", command=self._select_all).pack(side="left", padx=(0, 5))
        ttk.Button(btn_frame, text="Deselect All", command=self._deselect_all).pack(side="left")

        self.count_label = ttk.Label(btn_frame, text="")
        self.count_label.pack(side="right")

        # Scrollable channel list
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(list_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
        self.inner_frame = ttk.Frame(self.canvas)

        self.inner_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Status and progress
        status_frame = ttk.LabelFrame(self.root, text="Progress", padding=8)
        status_frame.pack(fill="x", padx=10, pady=5)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_frame, textvariable=self.status_var).pack(fill="x")

        self.progress = ttk.Progressbar(status_frame, mode="determinate")
        self.progress.pack(fill="x", pady=(5, 0))

        self.progress_label = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.progress_label).pack(fill="x")

        # Log area
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        log_frame.pack(fill="x", padx=10, pady=5)

        self.log_text = tk.Text(log_frame, height=6, state="disabled", wrap="word",
                                font=("Consolas", 9))
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side="left", fill="x", expand=True)
        log_scrollbar.pack(side="right", fill="y")

        # Action buttons
        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.start_btn = ttk.Button(action_frame, text="Start Deletion", command=self._start_deletion)
        self.start_btn.pack(side="left", padx=(0, 5))

        self.stop_btn = ttk.Button(action_frame, text="Stop", command=self._stop_deletion, state="disabled")
        self.stop_btn.pack(side="left")

        self.discover_btn = ttk.Button(action_frame, text="Discover New", command=self._start_discover)
        self.discover_btn.pack(side="right")

        self.refresh_btn = ttk.Button(action_frame, text="Refresh via API", command=self._start_refresh)
        self.refresh_btn.pack(side="right", padx=(0, 5))

    def _log(self, msg):
        """Append a line to the log area. Thread-safe via root.after."""
        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, _append)

    def _toggle_token(self):
        self.show_token = not self.show_token
        self.token_entry.configure(show="" if self.show_token else "*")
        self.toggle_btn.configure(text="Hide" if self.show_token else "Show")

    def _load_channels(self):
        self.status_var.set("Loading channels...")
        self.root.update()

        try:
            self.channels = load_channels()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load package data:\n{e}")
            self.status_var.set("Error loading data")
            return

        # Build channel list grouped by category
        current_category = None
        for ch in self.channels:
            if ch["category"] != current_category:
                current_category = ch["category"]
                header = ttk.Label(self.inner_frame, text=f"  {current_category}  ",
                                   font=("TkDefaultFont", 10, "bold"),
                                   foreground="#5865F2")
                header.pack(fill="x", pady=(10, 2), padx=5)
                ttk.Separator(self.inner_frame, orient="horizontal").pack(fill="x", padx=5)

            var = tk.BooleanVar(value=False)
            self.channel_vars[ch["id"]] = var

            text = f"{ch['display_name']}  ({ch['message_count']} msgs)"
            cb = ttk.Checkbutton(self.inner_frame, text=text, variable=var,
                                 command=self._update_count)
            cb.pack(fill="x", padx=20, pady=1)

        total = sum(c["message_count"] for c in self.channels)
        self.status_var.set(f"Loaded {len(self.channels)} channels, {total} total messages")
        self._update_count()

    def _update_count(self):
        selected = sum(
            ch["message_count"] for ch in self.channels
            if self.channel_vars.get(ch["id"], tk.BooleanVar()).get()
        )
        self.count_label.configure(text=f"{selected} msgs selected")

    def _select_all(self):
        for var in self.channel_vars.values():
            var.set(True)
        self._update_count()

    def _deselect_all(self):
        for var in self.channel_vars.values():
            var.set(False)
        self._update_count()

    def _start_deletion(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Token Required", "Please enter your Discord auth token.")
            return

        selected = [ch for ch in self.channels if self.channel_vars.get(ch["id"], tk.BooleanVar()).get()]
        if not selected:
            messagebox.showwarning("No Channels", "Please select at least one channel.")
            return

        total_msgs = sum(ch["message_count"] for ch in selected)
        if not messagebox.askyesno("Confirm", f"Delete {total_msgs} messages across {len(selected)} channels?"):
            return

        self.is_running = True
        self._set_buttons(True)
        self.progress["value"] = 0
        self.progress["maximum"] = total_msgs

        thread = threading.Thread(target=self._delete_worker, args=(token, selected, total_msgs), daemon=True)
        thread.start()

    def _set_buttons(self, running):
        """Enable/disable buttons based on whether a task is running."""
        state_on = "normal" if not running else "disabled"
        self.start_btn.configure(state=state_on)
        self.refresh_btn.configure(state=state_on)
        self.discover_btn.configure(state=state_on)
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _stop_deletion(self):
        self.is_running = False
        self.stop_btn.configure(state="disabled")
        self.status_var.set("Stopping...")

    def _start_refresh(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Token Required", "Please enter your Discord auth token.")
            return

        selected = [ch for ch in self.channels if self.channel_vars.get(ch["id"], tk.BooleanVar()).get()]
        if not selected:
            messagebox.showwarning("No Channels", "Please select at least one channel to check.")
            return

        total_msgs = sum(ch["message_count"] for ch in selected)
        self.is_running = True
        self._set_buttons(True)
        self.progress["value"] = 0
        self.progress["maximum"] = total_msgs

        thread = threading.Thread(target=self._refresh_worker, args=(token, selected, total_msgs), daemon=True)
        thread.start()

    def _start_discover(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Token Required", "Please enter your Discord auth token.")
            return

        self.is_running = True
        self._set_buttons(True)
        self.progress["value"] = 0
        self.progress.configure(mode="indeterminate")
        self.progress.start(20)

        thread = threading.Thread(target=self._discover_worker, args=(token,), daemon=True)
        thread.start()

    def _api_get(self, url, headers):
        """GET with rate limit handling. Returns response or None."""
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            self._log(f"  Rate limited, waiting {retry_after:.1f}s...")
            time.sleep(retry_after + 0.1)
            resp = requests.get(url, headers=headers, timeout=15)
        return resp

    def _fetch_user_messages(self, cid, user_id, headers):
        """Paginate through a channel and return list of message IDs authored by user_id."""
        message_ids = []
        before = None
        while self.is_running:
            url = f"{API_BASE}/channels/{cid}/messages?limit=100"
            if before:
                url += f"&before={before}"
            try:
                resp = self._api_get(url, headers)
                if resp.status_code in (403, 404):
                    break  # Can't access this channel
                if resp.status_code == 401:
                    self.is_running = False
                    break
                if resp.status_code != 200:
                    break
                msgs = resp.json()
                if not msgs:
                    break
                for m in msgs:
                    if m["author"]["id"] == user_id:
                        message_ids.append(m["id"])
                before = msgs[-1]["id"]
                time.sleep(0.5)
            except requests.RequestException:
                break
        return message_ids

    def _discover_worker(self, token):
        """Discover new channels and messages via the Discord API."""
        headers = {"Authorization": token}
        discovered = load_discovered()
        deleted_ids = load_deleted()
        known_msg_ids = set()
        for ch in self.channels:
            known_msg_ids.update(ch["message_ids"])
        for dch in discovered.values():
            known_msg_ids.update(dch["message_ids"])

        # Get user ID
        self._log("Fetching user info...")
        try:
            resp = self._api_get(f"{API_BASE}/users/@me", headers)
            if resp.status_code != 200:
                self._log(f"Failed to get user info (HTTP {resp.status_code})")
                self.root.after(0, self._finish_discover, 0, 0)
                return
            user_id = resp.json()["id"]
            self._log(f"User ID: {user_id}")
        except requests.RequestException as e:
            self._log(f"Error: {e}")
            self.root.after(0, self._finish_discover, 0, 0)
            return

        # Collect all channels to scan
        api_channels = []  # list of (id, display_name, category)

        # DMs and Group DMs
        self._log("Fetching DM channels...")
        try:
            resp = self._api_get(f"{API_BASE}/users/@me/channels", headers)
            if resp.status_code == 200:
                for ch in resp.json():
                    cid = ch["id"]
                    ch_type = ch.get("type", 0)
                    if ch_type == 1:  # DM
                        recip = ch.get("recipients", [{}])[0]
                        name = recip.get("username", "Unknown")
                        api_channels.append((cid, name, "Direct Messages"))
                    elif ch_type == 3:  # GROUP_DM
                        name = ch.get("name") or f"Group ({cid})"
                        api_channels.append((cid, name, "Group DMs"))
                self._log(f"  Found {len(api_channels)} DM/group channels")
            time.sleep(0.5)
        except requests.RequestException as e:
            self._log(f"  Error fetching DMs: {e}")

        # Guilds and their channels
        self._log("Fetching servers...")
        try:
            resp = self._api_get(f"{API_BASE}/users/@me/guilds", headers)
            if resp.status_code == 200:
                guilds = resp.json()
                self._log(f"  Found {len(guilds)} servers")
                for guild in guilds:
                    if not self.is_running:
                        break
                    gid = guild["id"]
                    gname = guild["name"]
                    time.sleep(0.5)
                    try:
                        resp2 = self._api_get(f"{API_BASE}/guilds/{gid}/channels", headers)
                        if resp2.status_code == 200:
                            for gch in resp2.json():
                                # type 0=text, 5=announcement
                                if gch.get("type", 0) in (0, 5):
                                    api_channels.append((gch["id"], gch.get("name", "unknown"), gname))
                    except requests.RequestException:
                        self._log(f"  Could not fetch channels for {gname}")
        except requests.RequestException as e:
            self._log(f"  Error fetching servers: {e}")

        self._log(f"Total channels to scan: {len(api_channels)}")

        # Switch progress to determinate
        self.root.after(0, self._set_discover_progress, len(api_channels))

        # Scan each channel for user's messages
        new_channels = 0
        new_messages = 0
        for i, (cid, name, category) in enumerate(api_channels):
            if not self.is_running:
                break

            self._log(f"Scanning: {name} ({category})")
            self.root.after(0, self.status_var.set, f"Scanning: {name}")

            msg_ids = self._fetch_user_messages(cid, user_id, headers)
            # Filter out known and deleted
            new_mids = [mid for mid in msg_ids if mid not in known_msg_ids and mid not in deleted_ids]

            if new_mids:
                new_messages += len(new_mids)
                self._log(f"  Found {len(new_mids)} new messages")
                known_msg_ids.update(new_mids)

                # Save to discovered
                if cid in discovered:
                    existing = set(discovered[cid]["message_ids"])
                    discovered[cid]["message_ids"].extend(m for m in new_mids if m not in existing)
                else:
                    new_channels += 1
                    discovered[cid] = {
                        "display_name": name,
                        "category": category,
                        "type": "DM" if category == "Direct Messages" else "GROUP_DM" if category == "Group DMs" else "GUILD_TEXT",
                        "message_ids": new_mids,
                    }
                save_discovered(discovered)
            else:
                self._log(f"  No new messages")

            self.root.after(0, self._update_progress, i + 1, len(api_channels), 0)
            time.sleep(0.5)

        self.root.after(0, self._finish_discover, new_channels, new_messages)

    def _set_discover_progress(self, total):
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=total, value=0)

    def _finish_discover(self, new_channels, new_messages):
        self.is_running = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_buttons(False)
        msg = f"Discover done: {new_messages} new messages"
        if new_channels:
            msg += f", {new_channels} new channels"
        self._log(msg)
        self.status_var.set(msg)

        if new_messages > 0:
            if messagebox.askyesno("Reload", f"Found {new_messages} new messages. Reload channel list?"):
                self._reload_channels()

    def _reload_channels(self):
        """Clear and rebuild the channel list."""
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self.channel_vars.clear()
        self.channels.clear()
        self._load_channels()

    def _refresh_worker(self, token, selected_channels, total_msgs):
        """Check selected channels via API — mark messages that are already gone."""
        headers = {"Authorization": token}
        checked = 0
        newly_deleted = 0
        still_exist = 0
        deleted_ids = load_deleted()

        for ch in selected_channels:
            if not self.is_running:
                break

            cid = ch["id"]
            self._log(f"--- Checking: {ch['display_name']} ({ch['message_count']} msgs) ---")
            self.root.after(0, self.status_var.set, f"Checking: {ch['display_name']}")

            for mid in ch["message_ids"]:
                if not self.is_running:
                    break

                url = f"{API_BASE}/channels/{cid}/messages/{mid}"
                try:
                    resp = requests.get(url, headers=headers, timeout=10)

                    if resp.status_code == 429:
                        retry_after = resp.json().get("retry_after", 5)
                        self._log(f"  Rate limited, waiting {retry_after:.1f}s...")
                        time.sleep(retry_after + 0.1)
                        resp = requests.get(url, headers=headers, timeout=10)

                    if resp.status_code == 200:
                        still_exist += 1
                        self._log(f"  Exists {mid}")
                    elif resp.status_code in (404, 403):
                        newly_deleted += 1
                        deleted_ids.add(mid)
                        save_deleted(deleted_ids)
                        self._log(f"  Gone {mid}")
                    elif resp.status_code == 401:
                        self._log("  Invalid token! Stopping.")
                        self.root.after(0, self.status_var.set, "Invalid token!")
                        self.is_running = False
                        break
                except requests.RequestException as e:
                    self._log(f"  ERROR {mid}: {e}")

                checked += 1
                self.root.after(0, self._update_progress, checked, total_msgs, 0)

                if self.is_running:
                    time.sleep(0.5)

        self.is_running = False
        self._log(f"Refresh done: {still_exist} still exist, {newly_deleted} already gone")
        self.root.after(0, self.status_var.set,
                        f"Refresh done: {still_exist} still exist, {newly_deleted} marked as deleted")
        self.root.after(0, self._set_buttons, False)

    def _delete_worker(self, token, selected_channels, total_msgs):
        headers = {"Authorization": token}
        deleted = 0
        errors = 0
        deleted_ids = load_deleted()

        for ch in selected_channels:
            if not self.is_running:
                break

            cid = ch["id"]
            self._log(f"--- {ch['display_name']} ({ch['message_count']} msgs) ---")
            self.root.after(0, self.status_var.set, f"Deleting in: {ch['display_name']}")

            for mid in ch["message_ids"]:
                if not self.is_running:
                    break

                url = f"{API_BASE}/channels/{cid}/messages/{mid}"
                try:
                    resp = requests.delete(url, headers=headers, timeout=10)

                    if resp.status_code == 429:
                        retry_after = resp.json().get("retry_after", 5)
                        self._log(f"  Rate limited, waiting {retry_after:.1f}s...")
                        self.root.after(0, self.status_var.set, f"Rate limited, waiting {retry_after:.1f}s...")
                        time.sleep(retry_after + 0.1)
                        resp = requests.delete(url, headers=headers, timeout=10)

                    if resp.status_code in (200, 204):
                        deleted += 1
                        deleted_ids.add(mid)
                        save_deleted(deleted_ids)
                        self._log(f"  Deleted {mid}")
                    elif resp.status_code in (403, 404):
                        deleted += 1
                        deleted_ids.add(mid)
                        save_deleted(deleted_ids)
                        self._log(f"  Skipped {mid} ({resp.status_code})")
                    else:
                        errors += 1
                        self._log(f"  FAILED {mid} (HTTP {resp.status_code})")
                        if resp.status_code == 401:
                            self._log("  Invalid token! Stopping.")
                            self.root.after(0, self.status_var.set, "Invalid token!")
                            self.is_running = False
                            break
                except requests.RequestException as e:
                    errors += 1
                    self._log(f"  ERROR {mid}: {e}")

                self.root.after(0, self._update_progress, deleted, total_msgs, errors)

                if self.is_running:
                    time.sleep(DELETE_DELAY)

        self.is_running = False
        final_msg = f"Done! Deleted {deleted}/{total_msgs} messages"
        if errors:
            final_msg += f" ({errors} errors)"
        self.root.after(0, self.status_var.set, final_msg)
        self.root.after(0, self._set_buttons, False)

    def _update_progress(self, deleted, total, errors):
        self.progress["value"] = deleted
        self.progress_label.set(f"{deleted}/{total} messages  ({errors} errors)" if errors else f"{deleted}/{total} messages")


def main():
    root = tk.Tk()
    app = DiscordDeleterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
