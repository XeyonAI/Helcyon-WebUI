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
@project_bp.route("/projects/get/<name>")
def get_project(name):
    try:
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, name)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        config_path = os.path.join(project_path, "config.json")
        
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {"name": name, "instructions": ""}
        
        return jsonify(config)
        
    except Exception as e:
        print(f"❌ Failed to get project: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Update project
# --------------------------------------------------
@project_bp.route("/projects/update/<name>", methods=["POST"])
def update_project(name):
    try:
        data = request.json
        
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, name)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        config_path = os.path.join(project_path, "config.json")
        
        # Load existing config
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {"name": name}
        
        # Update fields
        if "instructions" in data:
            config["instructions"] = data["instructions"]
        if "display_name" in data:
            config["name"] = data["display_name"]
        
        # Save updated config
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Updated project: {name}")
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
            # Switching to a project
            project_path = os.path.join(PROJECTS_DIR, name)
            
            if not os.path.exists(project_path):
                return jsonify({"error": "Project not found"}), 404
        
        # Set active project (None is valid for "no project")
        set_active_project(name)
        
        return jsonify({"success": True, "active": name})
        
    except Exception as e:
        print(f"❌ Failed to switch project: {e}")
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Delete project
# --------------------------------------------------
@project_bp.route("/projects/delete/<name>", methods=["DELETE"])
def delete_project(name):
    try:
        ensure_projects_dir()
        project_path = os.path.join(PROJECTS_DIR, name)
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Project not found"}), 404
        
        # Check if it's the active project
        if get_active_project() == name:
            # Clear active project
            set_active_project(None)
        
        # Delete the project directory
        import shutil
        shutil.rmtree(project_path)
        
        print(f"🗑️ Deleted project: {name}")
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
        
        # Validate file type
        allowed_extensions = {'.txt', '.md', '.pdf', '.docx', '.odt'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        
        if file_ext not in allowed_extensions:
            return jsonify({"error": f"File type not supported. Allowed: {', '.join(allowed_extensions)}"}), 400
        
        # Create documents directory
        project_path = os.path.join(PROJECTS_DIR, project_name)
        docs_dir = os.path.join(project_path, "documents")
        os.makedirs(docs_dir, exist_ok=True)
        
        # Save file
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