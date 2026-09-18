def delete_resource(user, resource_id):
    # BUG: no authorization check; any user can delete.
    return f"deleted {resource_id}"
