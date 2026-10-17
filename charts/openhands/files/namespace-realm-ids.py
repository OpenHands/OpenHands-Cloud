"""Make template UUIDs realm-specific; preserve the existing default realm IDs."""
import json
import re
import sys
import uuid


UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
OPAQUE_KEYS = {"secret", "clientSecret", "password", "credentials"}


def namespace_ids(text, realm):
    obj = json.loads(text)
    if realm == "allhands":
        return text
    identifiers = set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in OPAQUE_KEYS:
                    continue
                if key in {"id", "internalId"} and isinstance(child, str) and UUID.fullmatch(child):
                    identifiers.add(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(obj)
    mapping = {old: str(uuid.uuid5(uuid.NAMESPACE_URL, realm + old)) for old in identifiers}

    def replace(value):
        if isinstance(value, dict):
            return {key: child if key in OPAQUE_KEYS else replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    return json.dumps(replace(obj))


if __name__ == "__main__":
    sys.stdout.write(namespace_ids(sys.stdin.read(), sys.argv[1]))
