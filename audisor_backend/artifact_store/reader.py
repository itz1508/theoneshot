class ArtifactReader:
    def __init__(self, index):
        self.index = index

    def read(self, artifact_id: str):
        return self.index.get(artifact_id)

