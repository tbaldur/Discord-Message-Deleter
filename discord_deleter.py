import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import requests

PACKAGE_PATH = Path(__file__).parent / "package"
DELETED_FILE = Path(__file__).parent / "deleted.json"
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
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress["value"] = 0
        self.progress["maximum"] = total_msgs

        thread = threading.Thread(target=self._delete_worker, args=(token, selected, total_msgs), daemon=True)
        thread.start()

    def _stop_deletion(self):
        self.is_running = False
        self.stop_btn.configure(state="disabled")
        self.status_var.set("Stopping...")

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
        self.root.after(0, self.start_btn.configure, {"state": "normal"})
        self.root.after(0, self.stop_btn.configure, {"state": "disabled"})

    def _update_progress(self, deleted, total, errors):
        self.progress["value"] = deleted
        self.progress_label.set(f"{deleted}/{total} messages  ({errors} errors)" if errors else f"{deleted}/{total} messages")


def main():
    root = tk.Tk()
    app = DiscordDeleterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
