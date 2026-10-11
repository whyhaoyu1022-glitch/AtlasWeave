"""Build explicit straight-edge fixtures; no production operation is duplicated."""

from atlasweave.domain.topology import Edge, Face, HalfEdge, Ring, Topology, Vertex


class MapFixture:
    def __init__(self):
        self.vertices = {}
        self.edges = {}
        self.half_edges = {}
        self.rings = {}
        self.faces = {}
        self.vertex_at = {}
        self.edge_between = {}

    def ring(self, coordinates):
        vertex_ids = []
        for coordinate in coordinates:
            coordinate = tuple(coordinate)
            if coordinate not in self.vertex_at:
                vertex = Vertex(x=coordinate[0], y=coordinate[1])
                self.vertices[vertex.id] = vertex
                self.vertex_at[coordinate] = vertex.id
            vertex_ids.append(self.vertex_at[coordinate])
        half_ids = []
        for i, start in enumerate(vertex_ids):
            end = vertex_ids[(i + 1) % len(vertex_ids)]
            key = tuple(sorted((start, end)))
            if key not in self.edge_between:
                edge = Edge(start_vertex_id=start, end_vertex_id=end)
                self.edges[edge.id] = edge
                self.edge_between[key] = edge.id
            edge = self.edges[self.edge_between[key]]
            half = HalfEdge(edge_id=edge.id, reversed=edge.start_vertex_id != start)
            self.half_edges[half.id] = half
            half_ids.append(half.id)
        ring = Ring(half_edge_ids=tuple(half_ids))
        self.rings[ring.id] = ring
        return ring.id

    def face(self, outer, holes=()):
        face = Face(
            outer_ring_id=self.ring(outer), inner_ring_ids=tuple(self.ring(h) for h in holes)
        )
        self.faces[face.id] = face
        return face.id

    def topology(self):
        return Topology(
            vertices=self.vertices,
            edges=self.edges,
            half_edges=self.half_edges,
            rings=self.rings,
            faces=self.faces,
        )


def neighboring_squares():
    fixture = MapFixture()
    left = fixture.face([(0, 0), (1, 0), (1, 1), (0, 1)])
    right = fixture.face([(1, 0), (2, 0), (2, 1), (1, 1)])
    topology = fixture.topology()
    shared = (set(topology.edge_uses((left,))) & set(topology.edge_uses((right,)))).pop()
    return fixture, topology, left, right, shared
