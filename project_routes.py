# project_routes.py
import os
import json
from flask import Blueprint, jsonify, request
from datetime import datetime

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
                    "instructions": config.get("instructions", "")
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
        
        allowed_extensions = {'.txt', '.md', '.pdf', '.docx', '.odt'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        
        if file_ext not in allowed_extensions:
            return jsonify({"error": f"File type not supported. Allowed: {', '.join(allowed_extensions)}"}), 400
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        docs_dir = os.path.join(project_path, "documents")
        os.makedirs(docs_dir, exist_ok=True)
        
        filepath = os.path.join(docs_dir, file.filename)
        file.save(filepath)
        
        print(f"📄 Uploaded document: {file.filename} to {project_name}")
        return jsonify({"success": True, "filename": file.filename})
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# List documents in project
# --------------------------------------------------
@project_bp.route("/projects/<project_name>/documents/list")
def list_documents(project_name):
    try:
        project_path = os.path.join(PROJECTS_DIR, project_name)
        docs_dir = os.path.join(project_path, "documents")
        
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
        project_path = os.path.join(PROJECTS_DIR, project_name)
        docs_dir = os.path.join(project_path, "documents")
        filepath = os.path.join(docs_dir, filename)
        
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