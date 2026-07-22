# project_routes.py
import os
import re
import json
import requests
import shutil
import subprocess
from flask import Blueprint, jsonify, request
from datetime import datetime
from truncation import rough_token_count

print("✅ project_routes blueprint loaded")

project_bp = Blueprint("project_bp", __name__)
PROJECTS_DIR = os.path.join(os.getcwd(), "projects")

def ensure_projects_dir():
    """Ensure projects directory exists."""
    if not os.path.exists(PROJECTS_DIR):
        os.makedirs(PROJECTS_DIR)
        print(f"📁 Created projects directory: {PROJECTS_DIR}")

def get_active_project():
    """Get the currently active project name from state file."""
    state_file = os.path.join(PROJECTS_DIR, "_active_project.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("active_project")
        except Exception as e:
            print(f"⚠️ Failed to read active project: {e}")
    return None

def set_active_project(project_name):
    """Set the currently active project."""
    ensure_projects_dir()
    state_file = os.path.join(PROJECTS_DIR, "_active_project.json")
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"active_project": project_name}, f, indent=2)
        print(f"✅ Active project set to: {project_name}")
        return True
    except Exception as e:
        print(f"❌ Failed to set active project: {e}")
        return False

def _project_documents_dir(project_name):
    """Return the safe documents path for an existing project."""
    project_path = os.path.abspath(os.path.join(PROJECTS_DIR, project_name))
    projects_root = os.path.abspath(PROJECTS_DIR)

    if os.path.commonpath([projects_root, project_path]) != projects_root:
        raise ValueError("Invalid project path")
    if not os.path.isdir(project_path):
        raise FileNotFoundError("Project not found")

    docs_dir = os.path.join(project_path, "documents")
    os.makedirs(docs_dir, exist_ok=True)
    return docs_dir

def _allowed_project_document(filename):
    allowed_extensions = {'.txt', '.md', '.pdf', '.docx', '.odt'}
    return os.path.splitext(filename)[1].lower() in allowed_extensions

# --------------------------------------------------
# List all projects
# --------------------------------------------------
@project_bp.route("/projects/list")
def list_projects():
    ensure_projects_dir()
    
    try:
        projects = []
        for item in os.listdir(PROJECTS_DIR):
            project_path = os.path.join(PROJECTS_DIR, item)
            
            # Skip the state file
            if item == "_active_project.json":
                continue
            
            # Only include directories
            if os.path.isdir(project_path):
                config_path = os.path.join(project_path, "config.json")
                
                # Load project config if it exists
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                else:
                    config = {"name": item, "instructions": ""}
                
                projects.append({
                    "name": item,
                    "display_name": config.get("name", item),
                    "instructions": config.get("instructions", ""),
                    "rp_mode": config.get("rp_mode", False),
                    "rp_opener": config.get("rp_opener", ""),
                    "theme": config.get("theme", "")
                })
        
        return jsonify({"projects": projects, "active": get_active_project()})
        
    except Exception as e:
        print(f"❌ Failed to list projects: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Create new project
# --------------------------------------------------
@project_bp.route("/projects/create", methods=["POST"])
def create_project():
    try:
        data = request.json
        name = data.get("name", "").strip()
        instructions = data.get("instructions", "").strip()
        
        if not name:
            return jsonify({"error": "Project name required"}), 400
        
        # Sanitize project name for filesystem
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        
        if not safe_name:
            return jsonify({"error": "Invalid project name"}), 400
        
        ensure_projects_dir()
        
        project_path = os.path.join(PROJECTS_DIR, safe_name)
        
        if os.path.exists(project_path):
            return jsonify({"error": "Project already exists"}), 409
        
        # Create project directory structure
        os.makedirs(project_path)
        chats_dir = os.path.join(project_path, "chats")
        os.makedirs(chats_dir)
        
        # Create config file
        config = {
            "name": name,
            "instructions": instructions,
            "rp_mode": data.get("rp_mode", False),
            "rp_opener": data.get("rp_opener", "").strip(),
            "theme": data.get("theme", "").strip(),
            "created": datetime.now().isoformat()
        }
        
        config_path = os.path.join(project_path, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        # Set as active project
        set_active_project(safe_name)
        
        print(f"📁 Created project: {safe_name}")
        return jsonify({"success": True, "name": safe_name})
        
    except Exception as e:
        print(f"❌ Failed to create project: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Get project details
# --------------------------------------------------
@project_bp.route("/projects/get/<n>")
def get_project(n):
    try:
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, n)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        config_path = os.path.join(project_path, "config.json")
        
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {"name": n, "instructions": ""}
        
        return jsonify(config)
        
    except Exception as e:
        print(f"❌ Failed to get project: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Update project
# --------------------------------------------------
@project_bp.route("/projects/update/<n>", methods=["POST"])
def update_project(n):
    try:
        data = request.json
        
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, n)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        config_path = os.path.join(project_path, "config.json")
        
        # Load existing config
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {"name": n}
        
        # Update fields
        if "instructions" in data:
            config["instructions"] = data["instructions"]
        if "display_name" in data:
            config["name"] = data["display_name"]
        if "rp_mode" in data:
            config["rp_mode"] = data["rp_mode"]
        if "rp_opener" in data:
            config["rp_opener"] = data["rp_opener"].strip()
        if "theme" in data:
            config["theme"] = data["theme"].strip()
        
        # Save updated config
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Updated project: {n}")
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ Failed to update project: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Switch active project
# --------------------------------------------------
@project_bp.route("/projects/switch", methods=["POST"])
def switch_project():
    try:
        data = request.json
        name = data.get("name")
        
        ensure_projects_dir()
        
        if name:
            project_path = os.path.join(PROJECTS_DIR, name)
            
            if not os.path.exists(project_path):
                return jsonify({"error": "Project not found"}), 404
        
        set_active_project(name)
        
        return jsonify({"success": True, "active": name})
        
    except Exception as e:
        print(f"❌ Failed to switch project: {e}")
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Delete project
# --------------------------------------------------
@project_bp.route("/projects/delete/<n>", methods=["DELETE"])
def delete_project(n):
    try:
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, n)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        if get_active_project() == n:
            set_active_project(None)
        
        import shutil
        shutil.rmtree(project_path)
        
        print(f"🗑️ Deleted project: {n}")
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ Failed to delete project: {e}")
        return jsonify({"error": str(e)}), 500
        
        
# --------------------------------------------------
# Upload document to project
# --------------------------------------------------
@project_bp.route("/projects/<project_name>/documents/upload", methods=["POST"])
def upload_document(project_name):
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        filename = os.path.basename(file.filename)

        if not _allowed_project_document(filename):
            return jsonify({"error": "File type not supported. Allowed: .txt, .md, .pdf, .docx, .odt"}), 400
        
        docs_dir = _project_documents_dir(project_name)
        
        filepath = os.path.join(docs_dir, filename)
        file.save(filepath)
        
        print(f"Uploaded document: {filename} to {project_name}")
        return jsonify({"success": True, "filename": filename})
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({"error": str(e)}), 500

@project_bp.route("/projects/<project_name>/documents/upload_from_dialog", methods=["POST"])
def upload_document_from_dialog(project_name):
    try:
        docs_dir = _project_documents_dir(project_name)

        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            initialdir=docs_dir,
            title="Upload Project Document",
            filetypes=[
                ("Supported documents", "*.txt *.md *.pdf *.docx *.odt"),
                ("Text files", "*.txt *.md"),
                ("PDF files", "*.pdf"),
                ("Word documents", "*.docx"),
                ("OpenDocument text", "*.odt"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()

        if not selected:
            return jsonify({"cancelled": True})

        filename = os.path.basename(selected)
        if not _allowed_project_document(filename):
            return jsonify({"error": "File type not supported. Allowed: .txt, .md, .pdf, .docx, .odt"}), 400

        destination = os.path.join(docs_dir, filename)
        if not (os.path.exists(destination) and os.path.samefile(selected, destination)):
            shutil.copy2(selected, destination)

        print(f"Uploaded document from picker: {filename} to {project_name}")
        return jsonify({"success": True, "filename": filename})

    except Exception as e:
        print(f"Picker upload failed: {e}")
        return jsonify({"error": str(e)}), 500

@project_bp.route("/projects/<project_name>/documents/open_folder", methods=["POST"])
def open_project_documents_folder(project_name):
    try:
        docs_dir = _project_documents_dir(project_name)

        if os.name == "nt":
            os.startfile(docs_dir)
        elif os.name == "posix":
            opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([opener, docs_dir])
        else:
            return jsonify({"error": "Opening folders is not supported on this OS"}), 500

        return jsonify({"success": True, "path": docs_dir})

    except Exception as e:
        print(f"Failed to open project documents folder: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# List documents in project
# --------------------------------------------------
@project_bp.route("/projects/<project_name>/documents/list")
def list_documents(project_name):
    try:
        docs_dir = _project_documents_dir(project_name)
        
        if not os.path.exists(docs_dir):
            return jsonify({"documents": []})
        
        documents = []
        for filename in os.listdir(docs_dir):
            filepath = os.path.join(docs_dir, filename)
            if os.path.isfile(filepath):
                file_size = os.path.getsize(filepath)
                documents.append({
                    "filename": filename,
                    "size": file_size
                })
        
        return jsonify({"documents": documents})
        
    except Exception as e:
        print(f"❌ Failed to list documents: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Delete document from project
# --------------------------------------------------
@project_bp.route("/projects/<project_name>/documents/<filename>", methods=["DELETE"])
def delete_document(project_name, filename):
    try:
        docs_dir = _project_documents_dir(project_name)
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(docs_dir, safe_filename)
        
        if not os.path.exists(filepath):
            return jsonify({"error": "Document not found"}), 404
        
        os.remove(filepath)
        print(f"🗑️ Deleted document: {filename} from {project_name}")
        
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ Delete failed: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Toggle sticky docs
# --------------------------------------------------
@project_bp.route("/projects/toggle-sticky-docs/<project_name>", methods=["POST"])
def toggle_sticky_docs(project_name):
    """Toggle sticky document mode. Clears pinned doc when turning off.
    Auto-pins if only one document exists when turning on."""
    config_path = os.path.join(PROJECTS_DIR, project_name, "config.json")
    
    if not os.path.exists(config_path):
        return jsonify({"error": "Project not found"}), 404
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    current = config.get("sticky_docs", False)
    config["sticky_docs"] = not current
    
    # Clear pinned doc when turning sticky OFF
    if not config["sticky_docs"]:
        config["sticky_doc_file"] = None
        print(f"📌 Sticky docs disabled for: {project_name} - pinned doc cleared")
    else:
        # Turning ON - if only one doc exists, auto-pin it immediately
        docs_dir = os.path.join(PROJECTS_DIR, project_name, "documents")
        if os.path.exists(docs_dir):
            all_docs = [f for f in os.listdir(docs_dir) if os.path.isfile(os.path.join(docs_dir, f))]
            if len(all_docs) == 1:
                config["sticky_doc_file"] = all_docs[0]
                print(f"📌 Sticky docs enabled for: {project_name} - auto-pinned: {all_docs[0]}")
            else:
                print(f"📌 Sticky docs enabled for: {project_name} - waiting for doc trigger ({len(all_docs)} docs)")
        else:
            print(f"📌 Sticky docs enabled for: {project_name} - no documents folder yet")
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    
    return jsonify({
        "sticky_docs": config["sticky_docs"],
        "sticky_doc_file": config.get("sticky_doc_file")
    })


# --------------------------------------------------
# Get sticky docs state
# --------------------------------------------------
@project_bp.route("/projects/get-sticky-docs/<project_name>")
def get_sticky_docs(project_name):
    """Get sticky doc state and currently pinned filename.
    Auto-pins single doc if sticky is ON but nothing pinned yet."""
    config_path = os.path.join(PROJECTS_DIR, project_name, "config.json")
    
    if not os.path.exists(config_path):
        return jsonify({"sticky_docs": False, "sticky_doc_file": None})
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # If sticky is ON but nothing pinned, check if there's only one doc - auto-pin it
    if config.get("sticky_docs") and not config.get("sticky_doc_file"):
        docs_dir = os.path.join(PROJECTS_DIR, project_name, "documents")
        if os.path.exists(docs_dir):
            all_docs = [f for f in os.listdir(docs_dir) if os.path.isfile(os.path.join(docs_dir, f))]
            if len(all_docs) == 1:
                config["sticky_doc_file"] = all_docs[0]
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2)
                print(f"📌 Auto-pinned single doc on state read: {all_docs[0]}")
    
    return jsonify({
        "sticky_docs": config.get("sticky_docs", False),
        "sticky_doc_file": config.get("sticky_doc_file")
    })


# --------------------------------------------------
# Save pinned sticky doc (called by backend after successful trigger load)
# --------------------------------------------------
@project_bp.route("/projects/set-sticky-doc/<project_name>", methods=["POST"])
def set_sticky_doc(project_name):
    """Save which document got pinned by sticky mode."""
    config_path = os.path.join(PROJECTS_DIR, project_name, "config.json")
    
    if not os.path.exists(config_path):
        return jsonify({"error": "Project not found"}), 404
    
    data = request.json
    filename = data.get("filename")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    config["sticky_doc_file"] = filename
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    
    print(f"📌 Pinned doc set to: {filename} for project: {project_name}")
    return jsonify({"success": True, "sticky_doc_file": filename})


# --------------------------------------------------
# Project Groups (manual subfolders)
# _groups.json: { "groupName": ["projectName", ...], ... }
# --------------------------------------------------
GROUPS_FILE = os.path.join(os.getcwd(), "projects", "_groups.json")

def load_groups():
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_groups(groups):
    ensure_projects_dir()
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=2, ensure_ascii=False)

@project_bp.route("/projects/groups", methods=["GET"])
def get_groups():
    return jsonify(load_groups())

@project_bp.route("/projects/groups/save", methods=["POST"])
def save_groups_route():
    data = request.json
    groups = data.get("groups", {})
    save_groups(groups)
    print(f"📂 Groups saved: {list(groups.keys())}")
    return jsonify({"success": True})


# --------------------------------------------------
# Project folder colours (server-side persistence)
# project_colours.json: { "projectName": "#hexcolour", ... }
# --------------------------------------------------
# Stored server-side (not in localStorage) so folder colours survive switching
# between http/https, the Electron launcher, and browser-storage clears.
PROJECT_COLOURS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_colours.json")


def load_project_colours():
    if os.path.exists(PROJECT_COLOURS_FILE):
        try:
            with open(PROJECT_COLOURS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to read project colours: {e}")
    return {}


@project_bp.route("/projects/colours", methods=["GET"])
def get_project_colours():
    return jsonify(load_project_colours())


@project_bp.route("/projects/colours/save", methods=["POST"])
def save_project_colours():
    data = request.json or {}
    colours = data.get("colours", {})
    if not isinstance(colours, dict):
        return jsonify({"error": "colours must be an object"}), 400
    try:
        with open(PROJECT_COLOURS_FILE, "w", encoding="utf-8") as f:
            json.dump(colours, f, indent=2, ensure_ascii=False)
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    print(f"🎨 Project colours saved: {list(colours.keys())}")
    return jsonify({"success": True})


# --------------------------------------------------
# Global Documents — UI for the global_documents/ folder
# --------------------------------------------------
# These documents are keyword-matched and injected by load_global_documents()
# in app.py whenever a query matches their filename or leading 'Keywords:' line,
# regardless of the active project. That loader is unchanged — this section only
# adds a UI to upload (via /parse_document → text), edit, and delete them.
# Everything stored here is plain editable text (.txt/.md); the leading
# 'Keywords:' line is the retrieval tag and is stripped before injection.
# Matches app.py's resolution of the folder (same directory as this package).
GLOBAL_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_documents")


def ensure_global_docs_dir():
    if not os.path.exists(GLOBAL_DOCS_DIR):
        os.makedirs(GLOBAL_DOCS_DIR)
        print(f"🌐 Created global_documents directory: {GLOBAL_DOCS_DIR}")

@project_bp.route("/global_documents/open_folder", methods=["POST"])
def open_global_documents_folder():
    try:
        ensure_global_docs_dir()

        if os.name == "nt":
            os.startfile(GLOBAL_DOCS_DIR)
        elif os.name == "posix":
            opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([opener, GLOBAL_DOCS_DIR])
        else:
            return jsonify({"error": "Opening folders is not supported on this OS"}), 500

        return jsonify({"success": True, "path": GLOBAL_DOCS_DIR})

    except Exception as e:
        print(f"Failed to open global_documents folder: {e}")
        return jsonify({"error": str(e)}), 500


def _split_keywords_line(content):
    """Split an optional leading 'Keywords: a, b, c' line from the body.

    Mirrors app.py's _extract_doc_keywords convention: only the first few
    non-empty lines are scanned, case-insensitive. Returns (keywords, body).
    """
    lines = content.split('\n')
    seen = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        seen += 1
        if seen > 4:
            break
        m = re.match(r'^\s*keywords\s*[:;]\s*(.+)$', line.strip(), re.IGNORECASE)
        if m:
            kw = m.group(1).strip()
            body = '\n'.join(lines[:i] + lines[i + 1:]).strip()
            return kw, body
    return "", content.strip()


def _safe_doc_name(filename):
    """Sanitise a filename and force a .txt/.md extension (everything is editable text)."""
    safe = "".join(c for c in os.path.basename(filename)
                    if c.isalnum() or c in (' ', '-', '_', '.')).strip()
    if not safe:
        return None
    if not safe.lower().endswith(('.txt', '.md')):
        safe += '.txt'
    return safe


def _plain_chat_transcript(messages, limit=40):
    """Return a plain user/assistant transcript from recent chat messages."""
    lines = []
    for msg in (messages or [])[-limit:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content or "").strip()
        if content:
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
    return "\n\n".join(lines)


def _extract_json_object(text):
    """Extract the first JSON object from model text."""
    text = (text or "").strip()
    text = re.sub(r'<\|im_start\|>\w*', '', text)
    text = re.sub(r'<\|im_end\|>', '', text)
    text = re.sub(r'^\s*```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("Model did not return a JSON object")


def _clean_generated_document_text(text):
    text = (text or "").strip()
    text = re.sub(r'<\|im_start\|>\w*', '', text)
    text = re.sub(r'<\|im_end\|>', '', text)
    text = re.sub(r'^\s*```(?:json|markdown|md|text)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text)
    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if re.match(r"^#{0,3}\s*here['’]?s your save:?\s*$", cleaned, re.IGNORECASE):
            continue
        if re.match(r"^#{0,3}\s*here is your save:?\s*$", cleaned, re.IGNORECASE):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _strip_document_metadata_wrappers(text):
    text = _clean_generated_document_text(text)
    json_body = re.search(r'(?is)"body"\s*:\s*"?(.+?)"?\s*}\s*$', text)
    if json_body:
        text = json_body.group(1).strip()
    body_match = re.search(r"(?ims)^\s*body\s*:\s*(.+)\Z", text)
    if body_match:
        text = body_match.group(1).strip()
    lines = []
    skipping_keywords = False
    for line in text.splitlines():
        cleaned = line.strip()
        if not lines and not cleaned:
            continue
        if not lines:
            line = re.sub(r"^\s*(theory\s+summary|summary)\s*:\s*", "", line, flags=re.IGNORECASE)
            cleaned = line.strip()
        if not lines and re.match(r"^#{0,3}\s*save results:?\s*$", cleaned, re.IGNORECASE):
            continue
        if not lines and re.match(r"^#{0,3}\s*save notes:?\s*(.*)$", cleaned, re.IGNORECASE):
            remainder = re.sub(r"^#{0,3}\s*save notes:?\s*", "", line, flags=re.IGNORECASE).strip()
            if remainder:
                lines.append(remainder)
            continue
        if not lines and re.match(r"^#{0,3}\s*saved document contents:?\s*$", cleaned, re.IGNORECASE):
            continue
        if not lines and re.match(r"^(title|filename)\s*:\s*", cleaned, re.IGNORECASE):
            continue
        if not lines and re.match(r"^keywords\s*:\s*", cleaned, re.IGNORECASE):
            skipping_keywords = True
            continue
        if skipping_keywords:
            if not cleaned:
                skipping_keywords = False
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _title_case_topic(text):
    small_words = {
        "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
        "nor", "of", "on", "or", "the", "to", "with",
    }
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text or "")
    titled = []
    for i, word in enumerate(words[:12]):
        lower = word.lower()
        if i and lower in small_words:
            titled.append(lower)
        else:
            titled.append(lower[:1].upper() + lower[1:])
    return " ".join(titled).strip()


def _trim_topic_phrase(phrase):
    phrase = re.sub(r"[\{\}\[\]\"`]", " ", phrase or "")
    phrase = re.sub(r"\s+", " ", phrase).strip(" .,:;!?-")
    phrase = re.sub(r"^(theory\s+summary|summary)\s*:\s*", "", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"^human\s+understanding\s+of\s+", "", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"\s+is\s+(?:incomplete|not complete|wrong|incorrect)\b.*$", "", phrase, flags=re.IGNORECASE)
    phrase = re.sub(
        r"\b(rather than|instead of|because|where|which|including|through|before|after)\b.*$",
        "",
        phrase,
        flags=re.IGNORECASE,
    ).strip(" .,:;!?-")
    phrase = re.sub(r"^(the|a|an)\s+", "", phrase, flags=re.IGNORECASE)
    if len(phrase) > 80:
        phrase = phrase[:80].rsplit(" ", 1)[0]
    return phrase


def _derive_topic_title(body, transcript=""):
    body = _strip_document_metadata_wrappers(body)
    search_text = f"{body}\n{transcript}"[:5000]
    patterns = [
        r"\b(?:reframes|frames|describes|explores|covers|summarizes|summary of|about|regarding)\s+([^.\n;:]{8,120})",
        r"\b(?:topic|subject)\s*:\s*([^.\n;:]{8,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            phrase = _trim_topic_phrase(match.group(1))
            if phrase:
                return _title_case_topic(phrase)

    for line in body.splitlines():
        cleaned = re.sub(r"^[#*\-\s]+", "", line).strip()
        cleaned = re.sub(r"^(title|document title)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        if (
            cleaned
            and not _is_generic_document_title(cleaned)
            and not re.match(r"^(filename|keywords|body)\s*:", cleaned, re.IGNORECASE)
            and not cleaned.startswith("{")
            and len(cleaned) <= 90
        ):
            return _title_case_topic(_trim_topic_phrase(cleaned))

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        phrase = _trim_topic_phrase(sentence)
        if phrase and len(phrase) >= 8 and not _is_generic_document_title(phrase):
            return _title_case_topic(phrase)
    return "Reference Document"


def _filename_seed_from_title(title):
    seed = re.sub(r"[^A-Za-z0-9]+", "-", title or "").strip("-").lower()
    seed = re.sub(r"-{2,}", "-", seed)
    return seed[:70].strip("-") or "reference-document"


def _is_generic_document_title(title):
    title = re.sub(r"^[#*\-\s]+", "", str(title or "")).strip().lower()
    title = re.sub(r"[:.!]+$", "", title)
    generic = {
        "document",
        "implications",
        "key points",
        "notes",
        "overview",
        "reference document",
        "save notes",
        "save results",
        "saved document",
        "save document",
        "save as document",
        "summary",
        "theory summary",
        "here's your save",
        "here’s your save",
        "heres your save",
        "here is your save",
        "your save",
    }
    return not title or title in generic


def _derive_document_title(body):
    for line in (body or "").splitlines():
        line = re.sub(r"^[#*\-\s]+", "", line).strip()
        line = re.sub(r"^(title|document title)\s*:\s*", "", line, flags=re.IGNORECASE)
        if re.match(r"^(filename|keywords|body)\s*:", line, re.IGNORECASE):
            continue
        if line and not _is_generic_document_title(line) and not line.startswith("{"):
            return re.sub(r"\s+", " ", line)[:100]
    return "Reference Document"


def _extract_json_string_field(text, field):
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"]*)"', text or "", re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_json_array_field(text, field):
    match = re.search(rf'"{re.escape(field)}"\s*:\s*\[([^\]]*)\]', text or "", re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _extract_loose_body_field(text):
    match = re.search(r'"body"\s*:\s*"', text or "", re.DOTALL)
    if not match:
        return ""
    body = text[match.end():]
    body = re.sub(r'"\s*}\s*$', "", body.strip(), flags=re.DOTALL)
    body = re.sub(r'"\s*,?\s*$', "", body.strip(), flags=re.DOTALL)
    return body.strip()


def _extract_loose_document_payload(text):
    cleaned = _clean_generated_document_text(text)
    title = _extract_json_string_field(cleaned, "title")
    filename = _extract_json_string_field(cleaned, "filename")
    keywords = _extract_json_array_field(cleaned, "keywords")
    body = _extract_loose_body_field(cleaned)
    if not body:
        return None
    body = _strip_document_metadata_wrappers(body)
    if _is_generic_document_title(title):
        title = _derive_document_title(body)
    return {
        "title": title or _derive_document_title(body),
        "filename": filename or title or _derive_document_title(body),
        "keywords": keywords,
        "body": body,
    }


def _extract_labeled_document_payload(text):
    cleaned = _clean_generated_document_text(text)
    title_match = re.search(r"(?im)^\s*title\s*:\s*(.+?)\s*$", cleaned)
    filename_match = re.search(r"(?im)^\s*filename\s*:\s*(.+?)\s*$", cleaned)
    keywords_match = re.search(
        r"(?ims)^\s*keywords\s*:\s*(.+?)(?=^\s*(?:body|title|filename)\s*:|\Z)",
        cleaned,
    )
    body_match = re.search(r"(?ims)^\s*body\s*:\s*(.+)\Z", cleaned)
    if not body_match:
        return None

    body = _strip_document_metadata_wrappers(body_match.group(1))
    if not body:
        return None

    title = title_match.group(1).strip() if title_match else ""
    if _is_generic_document_title(title):
        title = _derive_document_title(body)
    filename = filename_match.group(1).strip() if filename_match else ""
    keywords = []
    if keywords_match:
        keywords = _clean_doc_keywords(keywords_match.group(1))

    return {
        "title": title or _derive_document_title(body),
        "filename": filename or title or _derive_document_title(body),
        "keywords": keywords,
        "body": body,
    }


def _derive_doc_keywords(title, body):
    stopwords = {
        "above", "about", "across", "after", "again", "against", "also", "and",
        "another", "around", "because", "been", "before", "being", "below",
        "between", "both", "but", "can", "chat", "common", "commonly",
        "concept", "conversation", "could", "describe", "described", "describes",
        "details", "document", "does",
        "done", "each", "every", "from", "get", "gets", "given", "had", "has",
        "happens", "have", "having", "here", "how", "into", "its", "just", "keeps", "may", "might",
        "mention", "mentions", "more", "most", "much", "need", "needs", "not",
        "note", "notes", "now", "often", "only", "other", "over", "page",
        "pages", "part", "point", "points", "presented",
        "reference", "regarding", "related", "results", "same", "save", "saved",
        "says", "should", "some", "something", "still", "stuff", "such", "summary",
        "than", "that", "the", "their", "them", "then", "there", "these", "this",
        "those", "through", "title", "too", "under", "use", "used", "user",
        "using", "view", "was", "way", "were", "what", "when", "where", "which",
        "while", "who", "why", "will", "with", "within", "without", "would",
        "body", "contents", "emphasis", "filename", "fresh", "human", "keywords",
        "key", "rather", "reframes", "theory",
    }
    phrase_allowlist = {
        "burr assembly", "character memory", "chosen experience", "global documents",
        "handwritten letter", "karma loop", "life purpose", "memory wipe",
        "purchase date", "replacement burr", "reset cycle", "search results",
        "serial number", "session summary", "soul identity", "web search",
    }
    weak_suffixes = ("ing", "ed")

    def clean_text(text):
        text = _strip_document_metadata_wrappers(text or "")
        text = re.sub(r"(?m)^\s*[-*]\s+", " ", text)
        text = re.sub(r"[\u2010-\u2015]+", "-", text)
        text = re.sub(r"\b([A-Za-z])-\s+([A-Za-z])\b", r"\1\2", text)
        text = re.sub(r"[_/\\|]+", " ", text)
        return text

    def tokenise(text):
        tokens = []
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9]*(?:'[A-Za-z0-9]+)?", clean_text(text)):
            raw = match.group(0).strip("'")
            lower = raw.lower()
            if len(lower) < 3 or lower in stopwords:
                continue
            if lower.endswith(weak_suffixes) and lower not in {"brewing", "training"}:
                continue
            if re.fullmatch(r"[a-z]+", lower) and len(lower) <= 3 and lower not in {"api", "tts", "web"}:
                continue
            tokens.append((lower, raw, match.start()))
        return tokens

    body_tokens = tokenise(body)
    title_tokens = tokenise(title)
    stats = {}

    def add_token(lower, raw, pos, source):
        entry = stats.setdefault(lower, {
            "count": 0,
            "body_count": 0,
            "title_count": 0,
            "first": pos,
            "display": raw,
        })
        entry["count"] += 1
        if source == "body":
            entry["body_count"] += 1
        else:
            entry["title_count"] += 1
        entry["first"] = min(entry["first"], pos)
        if raw.isupper() or (entry["display"].islower() and not raw.islower()):
            entry["display"] = raw

    for lower, raw, pos in body_tokens:
        add_token(lower, raw, pos, "body")
    offset = 100000
    for lower, raw, pos in title_tokens:
        add_token(lower, raw, offset + pos, "title")

    candidates = []
    for lower, entry in stats.items():
        score = entry["body_count"] * 8 + entry["title_count"] * 2
        if entry["body_count"] > 1:
            score += min(entry["body_count"], 5) * 3
        score += min(len(lower), 14) / 4
        display = entry["display"]
        if display.islower():
            display = display[:1].upper() + display[1:]
        candidates.append((score, entry["first"], lower, display[:50], {lower}))

    phrase_counts = {}
    phrase_first = {}
    for i in range(len(body_tokens) - 1):
        pair = body_tokens[i:i + 2]
        if pair[0][0] == pair[1][0]:
            continue
        phrase_key = " ".join(p[0] for p in pair)
        phrase_display = " ".join(p[1] for p in pair)
        phrase_counts[(phrase_key, phrase_display)] = phrase_counts.get((phrase_key, phrase_display), 0) + 1
        phrase_first.setdefault(phrase_key, pair[0][2])

    for (phrase_key, phrase_display), count in phrase_counts.items():
        parts = phrase_key.split()
        if any(part in stopwords for part in parts):
            continue
        if count < 2 and phrase_key not in phrase_allowlist:
            continue
        score = 12 + count * 8 + sum(stats.get(part, {}).get("body_count", 0) for part in parts) * 2
        if count > 1:
            score += 8
        display = re.sub(r"\s+", " ", phrase_display).strip()[:50]
        candidates.append((score, phrase_first.get(phrase_key, 99999), phrase_key, display, set(parts)))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    keywords = []
    used_terms = set()
    for _score, _first, key, display, parts in candidates:
        if key in used_terms:
            continue
        if len(parts) > 1 and any(part in used_terms for part in parts):
            continue
        if len(parts) == 1 and any(key in selected.split() for selected in used_terms):
            continue
        keywords.append(display)
        used_terms.add(key)
        if len(parts) > 1:
            used_terms.update(parts)
        if len(keywords) >= 10:
            break

    return keywords[:10]


def _document_payload_from_model_text(raw):
    try:
        return _extract_json_object(raw)
    except ValueError as e:
        labeled = _extract_labeled_document_payload(raw)
        if labeled:
            return labeled
        loose = _extract_loose_document_payload(raw)
        if loose:
            return loose
        body = _strip_document_metadata_wrappers(raw)
        if not body:
            raise e
        title = _derive_document_title(body)
        return {
            "title": title,
            "filename": title,
            "keywords": _derive_doc_keywords(title, body),
            "body": body,
        }


def _clean_doc_keywords(value):
    if isinstance(value, str):
        raw = re.split(r"[,;]+", value)
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    keywords = []
    for item in raw:
        kw = re.sub(r"[^A-Za-z0-9 '\-]", "", str(item)).strip()
        kw = re.sub(r"\s+", " ", kw)
        if kw and kw.lower() not in {k.lower() for k in keywords}:
            keywords.append(kw[:50])
        if len(keywords) >= 12:
            break
    return keywords


@project_bp.route("/global_documents/list")
def list_global_documents():
    """List global documents with their keywords and a short body preview."""
    ensure_global_docs_dir()
    docs = []
    try:
        for fn in sorted(os.listdir(GLOBAL_DOCS_DIR)):
            fp = os.path.join(GLOBAL_DOCS_DIR, fn)
            if not os.path.isfile(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except (UnicodeDecodeError, OSError):
                # A non-text file dropped in manually (e.g. a stray .pdf). Still
                # injected by the loader, but not editable in this UI.
                docs.append({
                    "filename": fn, "keywords": "",
                    "preview": "(non-text file — open the folder to edit)",
                    "editable": False, "size": os.path.getsize(fp),
                })
                continue
            kw, body = _split_keywords_line(content)
            preview = re.sub(r'\s+', ' ', body).strip()[:160]
            docs.append({
                "filename": fn, "keywords": kw, "preview": preview,
                "editable": True, "size": os.path.getsize(fp),
            })
        return jsonify({"documents": docs})
    except Exception as e:
        print(f"❌ Failed to list global documents: {e}")
        return jsonify({"error": str(e)}), 500


@project_bp.route("/global_documents/get/<path:filename>")
def get_global_document(filename):
    """Return a global document split into keywords + body for editing."""
    ensure_global_docs_dir()
    fp = os.path.join(GLOBAL_DOCS_DIR, os.path.basename(filename))
    if not os.path.isfile(fp):
        return jsonify({"error": "Document not found"}), 404
    try:
        with open(fp, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except (UnicodeDecodeError, OSError):
        return jsonify({"error": "File is not editable text"}), 400
    kw, body = _split_keywords_line(content)
    return jsonify({"filename": os.path.basename(filename), "keywords": kw, "body": body})


@project_bp.route("/global_documents/save", methods=["POST"])
def save_global_document():
    """Create or overwrite a global document.

    Stores `Keywords: …` as the leading line (the retrieval tag) followed by the
    body. `original_filename` (optional) lets an edit rename the file, deleting
    the old one. Always written as plain editable text.
    """
    ensure_global_docs_dir()
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    keywords = (data.get("keywords") or "").strip()
    body = (data.get("body") or "").strip()
    original = (data.get("original_filename") or "").strip()

    if not filename:
        return jsonify({"error": "Filename required"}), 400
    if not body:
        return jsonify({"error": "Document content required"}), 400

    safe = _safe_doc_name(filename)
    if not safe:
        return jsonify({"error": "Invalid filename"}), 400

    if keywords:
        content = f"Keywords: {keywords}\n\n{body}\n"
    else:
        content = body + "\n"

    fp = os.path.join(GLOBAL_DOCS_DIR, safe)
    try:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return jsonify({"error": str(e)}), 500

    # If an edit renamed the file, remove the old one.
    if original:
        old = os.path.join(GLOBAL_DOCS_DIR, os.path.basename(original))
        if os.path.basename(original) != safe and os.path.isfile(old):
            try:
                os.remove(old)
            except OSError:
                pass

    print(f"🌐 Saved global document: {safe} (keywords={'yes' if keywords else 'none'})")
    return jsonify({"success": True, "filename": safe})


@project_bp.route("/global_documents/save_from_chat", methods=["POST"])
def save_global_document_from_chat():
    """Generate and save a factual global reference document from recent chat."""
    from app_runtime_helpers import get_api_url, get_stop_tokens

    ensure_global_docs_dir()
    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages") or []
    user_name = (data.get("user_name") or "User").strip() or "User"
    instruction = (data.get("instruction") or "").strip()

    system_prompt = (
        "You create durable factual reference documents for a local chat application's "
        "global_documents folder. Write neutral informational material only. Do not "
        "write in character voice, do not write first-person diary or memory prose, "
        "and do not mention that you are saving a file. Use only information grounded "
        "in the provided transcript, including any web/search result text present in it. "
        "If the transcript is thin, make a concise reference note rather than inventing details.\n\n"
        "Return only the reference document body. Do not include JSON, filename, "
        "keywords, save-result labels, or metadata fields. Start with a concise "
        "topic heading, then write clear factual notes. Use short headings or "
        "bullet points when helpful."
    )
    transcript_limit = 40

    def _build_prompt(limit):
        transcript_text = _plain_chat_transcript(messages, limit=limit)
        if not transcript_text:
            return "", ""
        return transcript_text, (
            "<|im_start|>system\n"
            f"{system_prompt}\n"
            "<|im_end|>\n"
            "<|im_start|>user\n"
            f"User requesting save: {user_name}\n"
            f"Save command: {instruction or '(none)'}\n\n"
            "CONVERSATION TRANSCRIPT:\n"
            f"{transcript_text}\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

    transcript, prompt = _build_prompt(transcript_limit)
    if not transcript:
        return jsonify({"success": False, "error": "No conversation content to save."}), 400

    try:
        with open("settings.json", "r", encoding="utf-8") as sf:
            ctx_size = int((json.load(sf) or {}).get("llama_args", {}).get("ctx_size", 12288))
    except Exception:
        ctx_size = 12288
    gen_min = 256
    gen_target = 900
    est_real = int(rough_token_count(prompt) * 1.25)
    budget = ctx_size - gen_min
    while est_real > budget and transcript_limit > 6:
        transcript_limit -= 4
        transcript, prompt = _build_prompt(transcript_limit)
        est_real = int(rough_token_count(prompt) * 1.25)
    n_predict = min(gen_target, max(gen_min, ctx_size - est_real))

    try:
        payload = {
            "prompt": prompt,
            "temperature": 0.3,
            "n_predict": n_predict,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "stream": False,
            "stop": get_stop_tokens(),
        }
        resp = requests.post(f"{get_api_url()}/completion", json=payload, timeout=90)
        if resp.status_code >= 400:
            body = resp.text[:500] if resp.text else ""
            return jsonify({
                "success": False,
                "error": f"llama.cpp returned {resp.status_code}: {body or 'no body'}",
            }), 500
        raw = (resp.json() or {}).get("content", "").strip()
    except Exception as e:
        print(f"Failed to generate global document: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    body = _strip_document_metadata_wrappers(raw)
    if not body:
        return jsonify({"success": False, "error": "Generated document body was empty."}), 500

    title = _derive_topic_title(body, transcript)
    filename_seed = _filename_seed_from_title(title)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = _safe_doc_name(f"{filename_seed}-{timestamp}.txt")
    if not safe:
        safe = f"reference-document-{timestamp}.txt"

    keywords = _derive_doc_keywords(title, body) or _clean_doc_keywords(title)

    if title and not re.match(r"^#{1,3}\s+", body):
        body = f"# {title}\n\n{body}"

    content = f"Keywords: {', '.join(keywords)}\n\n{body.rstrip()}\n"
    fp = os.path.join(GLOBAL_DOCS_DIR, safe)
    try:
        with open(fp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        return jsonify({"success": False, "error": str(e)}), 500

    print(f"🌐 Saved generated global document: {safe} (keywords={len(keywords)})")
    return jsonify({
        "success": True,
        "filename": safe,
        "title": title,
        "keywords": keywords,
    })


@project_bp.route("/global_documents/<path:filename>", methods=["DELETE"])
def delete_global_document(filename):
    fp = os.path.join(GLOBAL_DOCS_DIR, os.path.basename(filename))
    if not os.path.isfile(fp):
        return jsonify({"error": "Document not found"}), 404
    try:
        os.remove(fp)
        print(f"🗑️ Deleted global document: {os.path.basename(filename)}")
        return jsonify({"success": True})
    except OSError as e:
        return jsonify({"error": str(e)}), 500
