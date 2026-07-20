class ArtifactWriter:
    def __init__(self, index):
        self.index = index

    def write(self, artifact_id: str, value):
        self.index.put(artifact_id, value)
        return artifact_id

