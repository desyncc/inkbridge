import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .client import ViwoodsClient
from .config import Config
from .jsonstore import read_json, update_json
from .ocr import OCREngine
from .vault import ObsidianVault

STATE_FILE = Path(__file__).resolve().parent.parent / ".viwoods_sync_state.json"
LOCAL_CACHE_DIR = Path(__file__).resolve().parent.parent / ".viwoods_cache"


def _page_sort_key(page: Dict[str, Any]):
    """Orders pages by pageNo, keeping unnumbered pages last in API order."""
    try:
        return (0, int(page.get("pageNo")))
    except (TypeError, ValueError):
        return (1, 0)


EMPTY_STATE = {"notes": {}, "last_full_sync": None}


def load_sync_state() -> dict:
    """Reads the on-disk sync state. Shared with the exporter and the server."""
    data = read_json(STATE_FILE, EMPTY_STATE)
    if not isinstance(data.get("notes"), dict):
        data["notes"] = {}
    data.setdefault("last_full_sync", None)
    return data


class SyncEngine:
    def __init__(
        self,
        config: Config,
        client: Optional[ViwoodsClient] = None,
        ocr: Optional[OCREngine] = None,
        vault: Optional[ObsidianVault] = None
    ):
        self.config = config
        self.client = client or ViwoodsClient(config)
        self.ocr = ocr or OCREngine(config)
        self.vault = vault or ObsidianVault(config)
        self.state = self._load_state()
        # Notes updated by this run, merged into the file on each save.
        self._dirty_notes: set = set()

        LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict:
        return load_sync_state()

    def _save_state(self):
        """
        Merges the notes this run touched into whatever is on disk now, so a
        concurrent CLI sync and dashboard sync do not erase each other's work.
        """
        dirty_notes = {
            uuid: self.state["notes"][uuid]
            for uuid in self._dirty_notes
            if uuid in self.state["notes"]
        }
        last_full_sync = self.state.get("last_full_sync")

        def mutate(disk_state: dict) -> None:
            notes = disk_state.setdefault("notes", {})
            if not isinstance(notes, dict):
                notes = disk_state["notes"] = {}
            notes.update(dirty_notes)
            # Never move last_full_sync backwards (ISO strings sort correctly).
            if last_full_sync and last_full_sync > (disk_state.get("last_full_sync") or ""):
                disk_state["last_full_sync"] = last_full_sync

        try:
            self.state = update_json(STATE_FILE, mutate, EMPTY_STATE)
            self._dirty_notes.clear()
        except Exception as e:
            print(f"Warning: Failed to save sync state: {e}")

    def is_date_str(self, text: str) -> Optional[str]:
        """Detects if a notebook name is formatted as YYYY-MM-DD or MM-DD-YYYY."""
        # Check YYYY-MM-DD
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text.strip())
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"

        # Check MM-DD-YYYY
        m2 = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", text.strip())
        if m2:
            mo, d, y = m2.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"

        return None

    def sync_notebook(
        self,
        item: Dict[str, Any],
        rel_folder_path: str,
        app_type: int = 1,
        force: bool = False,
        progress_cb: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """Syncs a single notebook: downloads pages, transcribes handwriting, and updates vault."""
        uuid = item.get("uuid") or item.get("resourceId")
        note_name = item.get("name") or "Untitled"
        last_modified = item.get("lastModifiedTime") or item.get("updatedAt") or 0

        note_state = self.state["notes"].get(uuid, {})
        cached_mod = note_state.get("last_modified", 0)

        # Skip if unmodified and not forced. Page count is deliberately not
        # part of this test: a genuinely empty note would otherwise be
        # re-fetched and re-processed on every single sync.
        if not force and cached_mod and last_modified and last_modified <= cached_mod:
            if progress_cb:
                progress_cb(f"Skipping unmodified note: {note_name}", 1.0)
            return False

        if progress_cb:
            progress_cb(f"Fetching note: {note_name}...", 0.1)

        try:
            detail = self.client.get_paper_detail(uuid, app_type=app_type)
        except Exception as e:
            print(f"Error fetching note '{note_name}' ({uuid}): {e}")
            return False

        # The API returns pages in its own order, so cap by page number, not
        # by whatever came back first.
        image_pages = sorted(detail.get("imagePages", []), key=_page_sort_key)
        total_pages = len(image_pages)
        max_allowed = getattr(self.config, "max_pages_per_notebook", 50)
        if max_allowed > 0 and total_pages > max_allowed:
            image_pages = image_pages[:max_allowed]
            print(
                f"Note '{note_name}': {max_allowed} of {total_pages} pages synced "
                f"(max_pages_per_notebook={max_allowed})."
            )

        pages_data = []

        for p_idx, page in enumerate(image_pages, start=1):
            page_no = page.get("pageNo", p_idx)
            img_url = page.get("imageUrl") or page.get("pageImageUrl")
            direct_content = page.get("content", "").strip()

            if progress_cb:
                pct = 0.2 + (0.7 * (p_idx / max(1, len(image_pages))))
                progress_cb(f"Processing '{note_name}' (page {page_no}/{len(image_pages)} of {total_pages})...", pct)

            local_img_path = ""
            transcript = direct_content

            if img_url:
                local_cache_img = LOCAL_CACHE_DIR / f"{uuid}_p{page_no}.png"
                if not local_cache_img.exists() or force:
                    try:
                        self.client.download_file(img_url, str(local_cache_img))
                    except Exception as e:
                        print(f"Error downloading page {page_no} for {note_name}: {e}")

                if local_cache_img.exists():
                    dest_name = self.vault.attachment_filename(note_name, uuid, page_no)
                    local_img_path = self.vault.save_attachment(str(local_cache_img), dest_name)
                    if not transcript:
                        transcript = self.ocr.transcribe(
                            str(local_cache_img),
                            context_prompt=f"Notebook: {note_name}",
                            force=force
                        )

            pages_data.append({
                "pageNo": page_no,
                "local_image_path": local_img_path,
                "transcript": transcript,
                "raw_page": page
            })

        recordings = detail.get("recordings") or []
        if recordings and getattr(self.config, "download_recordings", True):
            self._download_recordings(recordings, uuid, note_name, force=force)

        # The detail payload carries no creation time; the folder listing does.
        metadata = dict(detail)
        metadata["created_time"] = next(
            (item.get(k) for k in ("createTime", "createdAt", "create_time", "creationTime")
             if item.get(k)),
            None
        )
        metadata["total_pages_available"] = total_pages
        metadata["page_cap"] = max_allowed

        # 1. Mirror into Obsidian Viwoods/ directory
        self.vault.mirror_notebook(
            rel_folder_path=rel_folder_path,
            notebook_name=note_name,
            uuid=uuid,
            pages_data=pages_data,
            metadata=metadata
        )

        # 2. Check if this is a Journal entry that should inject into 10 - Journals
        date_str = self.is_date_str(note_name)
        is_in_journal_folder = "journal" in rel_folder_path.lower()
        if date_str or is_in_journal_folder:
            target_date = date_str
            if not target_date:
                # Try inferring from creation/update time
                ctime = detail.get("lastModifiedTime") or item.get("createdAt")
                if ctime:
                    try:
                        target_date = datetime.fromtimestamp(ctime / 1000).strftime("%Y-%m-%d")
                    except Exception:
                        pass

            if target_date:
                if progress_cb:
                    progress_cb(f"Injecting into daily journal for {target_date}...", 0.95)
                self.vault.sync_daily_journal(
                    target_date, pages_data, raw_meta=detail, note_uuid=uuid
                )

        # Update state
        self.state["notes"][uuid] = {
            "name": note_name,
            "app_type": app_type,
            "last_modified": last_modified,
            "pages_count": len(pages_data),
            "total_pages": total_pages,
            "synced_at": datetime.now().isoformat()
        }
        self._dirty_notes.add(uuid)
        self._save_state()

        if progress_cb:
            progress_cb(f"Finished: {note_name}", 1.0)
        return True

    @staticmethod
    def _recording_url(recording: Dict[str, Any]) -> Optional[str]:
        """Digs the audio URL out of a recording entry, whatever shape it has."""
        for key in ("fileURL", "fileUrl", "url", "recordingUrl", "audioUrl"):
            value = recording.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        for key in (
            "cloudRecordingFileInfo", "recordingFileInfo",
            "serviceRecordingFileInfo", "cloudAudioFileInfo", "audioFileInfo"
        ):
            info = recording.get(key)
            if isinstance(info, dict):
                value = info.get("fileURL") or info.get("fileUrl") or info.get("url")
                if isinstance(value, str) and value.startswith("http"):
                    return value
        return None

    def _download_recordings(
        self,
        recordings: List[Dict[str, Any]],
        uuid: str,
        note_name: str,
        force: bool = False
    ) -> None:
        """
        Saves each recording into the vault attachments folder and records its
        path as local_audio_path, which mirror_notebook() links.
        """
        for idx, rec in enumerate(recordings, start=1):
            if not isinstance(rec, dict):
                continue

            url = self._recording_url(rec)
            if not url:
                continue

            ext = os.path.splitext(urlparse(url).path)[1]
            if len(ext) > 5 or not ext:
                ext = ".m4a"

            cache_file = LOCAL_CACHE_DIR / f"{uuid}_rec{idx}{ext}"
            if not cache_file.exists() or force:
                try:
                    self.client.download_file(url, str(cache_file))
                except Exception as e:
                    print(f"Error downloading recording {idx} for {note_name}: {e}")
                    continue

            if cache_file.exists():
                dest_name = self.vault.attachment_filename(
                    note_name, uuid, idx, ext=ext, kind="rec"
                )
                rec["local_audio_path"] = self.vault.save_attachment(str(cache_file), dest_name)

    def sync_folder(
        self,
        app_type: int,
        folder_name: str,
        resource_id: str = "",
        sub_tab: int = -1,
        rel_path: str = "",
        force: bool = False,
        visited: Optional[set] = None,
        progress_cb: Optional[Callable[[str, float], None]] = None
    ) -> int:
        """Recursively traverses a folder and mirrors all subfolders and notebooks."""
        if visited is None:
            visited = set()

        folder_key = (app_type, sub_tab, resource_id, folder_name)
        if folder_key in visited:
            return 0
        visited.add(folder_key)

        current_rel = f"{rel_path}/{folder_name}".strip("/") if rel_path else folder_name
        if progress_cb:
            progress_cb(f"Scanning folder: {current_rel}...", 0.05)

        items = self.client.get_folder_items(app_type=app_type, resource_id=resource_id, sub_tab=sub_tab)
        synced_count = 0

        for it in items:
            rtype = it.get("resourceType")
            name = it.get("name") or "Untitled"
            item_uuid = it.get("uuid") or it.get("resourceId") or ""
            item_subtab = it.get("subTab", -1) if it.get("subTab") is not None else -1

            # Skip template directories and background images (they are not user notes)
            if item_uuid.startswith("NOTE_TEMPLATE_") or item_uuid.startswith("TEMPLATE_IMAGE_") or name.lower().endswith(".png"):
                continue

            # Check if directory / folder (type 0 or 5)
            if rtype in (0, 5):
                # If Knowledge Base subTab or subfolder
                next_subtab = item_subtab if (app_type == 4 and item_subtab != -1) else -1
                # If resourceId is empty and no subtab change, avoid looping
                if not item_uuid and next_subtab == sub_tab:
                    continue

                sub_count = self.sync_folder(
                    app_type=app_type,
                    folder_name=name,
                    resource_id=item_uuid,
                    sub_tab=next_subtab,
                    rel_path=current_rel,
                    force=force,
                    visited=visited,
                    progress_cb=progress_cb
                )
                synced_count += sub_count
            else:
                # Regular notebook / note
                success = self.sync_notebook(
                    item=it,
                    rel_folder_path=current_rel,
                    app_type=app_type,
                    force=force,
                    progress_cb=progress_cb
                )
                if success:
                    synced_count += 1

        return synced_count

    def sync_all(
        self,
        force: bool = False,
        progress_cb: Optional[Callable[[str, float], None]] = None,
        app_types: Optional[Iterable[int]] = None
    ) -> Dict[str, Any]:
        """
        Performs a full sync across the root apps (Paper, Meeting, Learning,
        Knowledge Base, Memo), or only `app_types` when given.
        """
        if progress_cb:
            progress_cb("Connecting to Viwoods Cloud...", 0.01)

        wanted = set(app_types) if app_types is not None else None

        # Refresh device info
        self.client.get_devices()
        root_folders = self.client.get_root_folders()

        total_synced = 0
        details = {}

        for rf in root_folders:
            name = rf.get("name", "Unknown")
            app_type = rf.get("appType", 1)
            count = rf.get("count", 0)

            if wanted is not None and app_type not in wanted:
                continue

            # Skip apps with 0 items
            if count == 0:
                continue

            if progress_cb:
                progress_cb(f"Syncing {name} ({count} items)...", 0.05)

            synced = self.sync_folder(
                app_type=app_type,
                folder_name=name,
                resource_id="",
                rel_path="",
                force=force,
                progress_cb=progress_cb
            )
            total_synced += synced
            details[name] = synced

        self.state["last_full_sync"] = datetime.now().isoformat()
        self._save_state()

        if progress_cb:
            progress_cb(f"Sync completed! {total_synced} notes updated.", 1.0)

        return {
            "total_synced": total_synced,
            "details": details,
            "timestamp": self.state["last_full_sync"]
        }

    def find_resource_location(
        self,
        app_type: int,
        resource_id: str,
        max_depth: int = 3
    ) -> Optional[Dict[str, str]]:
        """
        Breadth-first search for a folder by id, returning its name and the
        mirror path of its parent, so a partial sync lands where a full sync
        would put it.
        """
        root_name = next(
            (rf.get("name", f"App{app_type}") for rf in self.client.get_root_folders()
             if rf.get("appType") == app_type),
            f"App{app_type}"
        )

        queue = [("", root_name, 0)]  # (folder id, mirror path so far, depth)
        while queue:
            folder_id, rel_path, depth = queue.pop(0)
            if depth > max_depth:
                continue
            try:
                items = self.client.get_folder_items(app_type=app_type, resource_id=folder_id)
            except Exception:
                continue

            for it in items:
                item_id = it.get("uuid") or it.get("resourceId") or ""
                if item_id == resource_id:
                    return {"name": it.get("name") or resource_id, "rel_path": rel_path}
                if it.get("resourceType") in (0, 5) and item_id:
                    queue.append((item_id, f"{rel_path}/{it.get('name') or item_id}", depth + 1))

        return None

    def sync_resource(
        self,
        app_type: int,
        resource_id: str,
        force: bool = False,
        progress_cb: Optional[Callable[[str, float], None]] = None
    ) -> int:
        """Syncs a single folder subtree instead of everything."""
        location = self.find_resource_location(app_type, resource_id)
        if location:
            folder_name, rel_path = location["name"], location["rel_path"]
        else:
            folder_name, rel_path = resource_id, next(
                (rf.get("name", f"App{app_type}") for rf in self.client.get_root_folders()
                 if rf.get("appType") == app_type),
                f"App{app_type}"
            )

        return self.sync_folder(
            app_type=app_type,
            folder_name=folder_name,
            resource_id=resource_id,
            rel_path=rel_path,
            force=force,
            progress_cb=progress_cb
        )

    def find_journal_folder(self) -> Optional[Dict[str, Any]]:
        """Locates the 'Journals' folder in the Paper root, if the user has one."""
        paper_items = self.client.get_folder_items(app_type=1, resource_id="")
        for it in paper_items:
            if it.get("resourceType") in (0, 5) and "journal" in (it.get("name") or "").lower():
                return it
        return None

    def list_journal_items(self) -> List[Dict[str, Any]]:
        """Returns the entries inside the Journals folder (empty if there is none)."""
        journal_folder = self.find_journal_folder()
        if not journal_folder:
            return []
        j_uuid = journal_folder.get("uuid") or journal_folder.get("resourceId")
        return self.client.get_folder_items(app_type=1, resource_id=j_uuid)

    def sync_recent_journals(
        self,
        days_back: int = 14,
        force: bool = False,
        progress_cb: Optional[Callable[[str, float], None]] = None
    ) -> int:
        """Syncs recent journal entries from the 'Journals' folder."""
        if progress_cb:
            progress_cb(f"Scanning for journal entries (last {days_back} days)...", 0.05)

        items = self.list_journal_items()
        if not items:
            return 0

        cutoff = None
        if days_back > 0:
            cutoff = datetime.now() - timedelta(days=days_back)

        synced = 0
        for it in items:
            name = it.get("name") or ""
            date_str = self.is_date_str(name)

            if cutoff and date_str:
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    if d < cutoff:
                        continue
                except Exception:
                    pass

            success = self.sync_notebook(
                item=it,
                rel_folder_path="Paper/Journals",
                app_type=1,
                force=force,
                progress_cb=progress_cb
            )
            if success:
                synced += 1

        return synced
