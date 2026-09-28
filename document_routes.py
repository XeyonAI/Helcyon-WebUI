"""HTTP surface for the sandboxed document tool layer.

Thin wrapper: every route delegates straight to document_tools, which owns the
security boundary. Nothing here resolves a path itself, and nothing here accepts
a location that is not one of the three permitted root keys.

`/documents/delete` requires an explicit `confirmed_by_user` flag in the request
body. The automatic model path never sets it — only a deliberate user action
does — so an inferred intent cannot remove a file.
"""

from flask import Blueprint, request, jsonify

import document_tools

document_bp = Blueprint("documents", __name__)


def _payload():
    return request.get_json(silent=True) or {}


@document_bp.route("/documents/list", methods=["GET", "POST"])
def documents_list():
    data = _payload()
    root = request.args.get("root") or data.get("root")
    result = document_tools.run_document_action("list", root=root)
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/read", methods=["POST"])
def documents_read():
    data = _payload()
    result = document_tools.run_document_action(
        "read", root=data.get("root"), name=data.get("name")
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/create", methods=["POST"])
def documents_create():
    data = _payload()
    result = document_tools.run_document_action(
        "create", root=data.get("root"), name=data.get("name"),
        content=data.get("content", ""), overwrite=data.get("overwrite", False),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/update", methods=["POST"])
def documents_update():
    data = _payload()
    result = document_tools.run_document_action(
        "update", root=data.get("root"), name=data.get("name"),
        content=data.get("content", ""),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/edit", methods=["POST"])
def documents_edit():
    data = _payload()
    result = document_tools.run_document_action(
        "edit", root=data.get("root"), name=data.get("name"),
        find=data.get("find", ""), replace=data.get("replace", ""),
        count=data.get("count", 0),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/append", methods=["POST"])
def documents_append():
    data = _payload()
    result = document_tools.run_document_action(
        "append", root=data.get("root"), name=data.get("name"),
        text=data.get("text", ""), after=data.get("after"),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/save_as", methods=["POST"])
def documents_save_as():
    data = _payload()
    result = document_tools.run_document_action(
        "save_as", root=data.get("root"), name=data.get("name"),
        new_name=data.get("new_name"), new_root=data.get("new_root"),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/rename", methods=["POST"])
def documents_rename():
    data = _payload()
    result = document_tools.run_document_action(
        "rename", root=data.get("root"), name=data.get("name"),
        new_name=data.get("new_name"),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@document_bp.route("/documents/delete", methods=["POST"])
def documents_delete():
    data = _payload()
    # ⚠️ No default. An absent flag is a refusal, not a permission.
    result = document_tools.run_document_action(
        "delete", root=data.get("root"), name=data.get("name"),
        confirmed_by_user=bool(data.get("confirmed_by_user")),
    )
    return jsonify(result), (200 if result.get("ok") else 400)
