class ArtifactIndex:
    def __init__(self):
        self._items = {}

    def put(self, artifact_id: str, value):
        self._items[artifact_id] = value

    def get(self, artifact_id: str):
        return self._items[artifact_id]

