import torch
import torch.nn as nn
import torch.nn.functional as F

import dgl
from dgl.nn.pytorch import GATConv
from . import BaseModel, register_model
from .macro_layer.SemanticConv import SemanticAttention

@register_model('HAN')
class HAN(BaseModel):
    r"""
    Description
    ------------
    This model shows an example of using dgl.metapath_reachable_graph on the original heterogeneous
    graph.Because the original HAN implementation only gives the preprocessed homogeneous graph, this model
    could not reproduce the result in HAN as they did not provide the preprocessing code, and we
    constructed another dataset from ACM with a different set of papers, connections, features and
    labels.
    Parameter
    ------------
    meta_paths : list
        contain multiple meta-paths.
    category : str
        The category means the head and tail node of metapaths
    """
    @classmethod
    def build_model_from_args(cls, args, hg):
        etypes = hg.canonical_etypes
        mps = []
        for etype in etypes:
            if etype[0] == args.category:
                for dst_e in etypes:
                    if etype[0] == dst_e[2] and etype[2] == dst_e[0]:
                        if etype[0] != etype[2]:
                            mps.append([etype, dst_e])

        return cls(meta_paths=mps, category=args.category,
                    in_size=args.in_dim, hidden_size=args.hidden_dim,
                    out_size=args.out_dim,
                    num_heads=args.num_heads,
                    dropout=args.dropout)

    def __init__(self, meta_paths, category, in_size, hidden_size, out_size, num_heads, dropout):
        super(HAN, self).__init__()
        self.category = category
        self.layers = nn.ModuleList()
        self.layers.append(HANLayer(meta_paths, in_size, hidden_size, num_heads[0], dropout))
        for l in range(1, len(num_heads)):
            self.layers.append(HANLayer(meta_paths, hidden_size * num_heads[l-1],
                                        hidden_size, num_heads[l], dropout))
        self.linear = nn.Linear(hidden_size * num_heads[-1], out_size)

    def forward(self, g, h=None):

        h = g.nodes[self.category].data['h']
        for gnn in self.layers:
            h = gnn(g, h)

        return {self.category: self.linear(h)}


class HANLayer(nn.Module):
    """
    HAN layer.
    Parameters
    -----------
    meta_paths : list of metapaths, each as a list of edge types
    in_size : input feature dimension
    out_size : output feature dimension
    layer_num_heads : number of attention heads
    dropout : Dropout probability
    Inputs
    ------
    g : DGLHeteroGraph
        The heterogeneous graph
    h : tensor
        Input features
    Outputs
    -------
    tensor
        The output feature
    """
    def __init__(self, meta_paths, in_size, out_size, layer_num_heads, dropout):
        super(HANLayer, self).__init__()

        # One GAT layer for each meta path based adjacency matrix
        self.gat_layers = nn.ModuleList()
        for i in range(len(meta_paths)):
            self.gat_layers.append(GATConv(in_size, out_size, layer_num_heads,
                                           dropout, dropout, activation=F.elu,
                                           allow_zero_in_degree=True))
        self.semantic_attention = SemanticAttention(in_size=out_size * layer_num_heads)
        self.meta_paths = list(tuple(meta_path) for meta_path in meta_paths)

        self._cached_graph = None
        self._cached_coalesced_graph = {}

    def forward(self, g, h):
        semantic_embeddings = []

        if self._cached_graph is None or self._cached_graph is not g:
            self._cached_graph = g
            self._cached_coalesced_graph.clear()
            for meta_path in self.meta_paths:
                self._cached_coalesced_graph[meta_path] = dgl.metapath_reachable_graph(
                        g, meta_path)

        for i, meta_path in enumerate(self.meta_paths):
            new_g = self._cached_coalesced_graph[meta_path]
            semantic_embeddings.append(self.gat_layers[i](new_g, h).flatten(1))
        semantic_embeddings = torch.stack(semantic_embeddings, dim=1)                  # (N, M, D * K)

        return self.semantic_attention(semantic_embeddings)                            # (N, D * K)