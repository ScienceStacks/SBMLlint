"""Mass Equality Set Structure Analysis with Gaussian Elimination (MESSAGE)"""

from SBMLLint.common import constants as cn
from SBMLLint.common.molecule import Molecule, MoleculeStoichiometry
from SBMLLint.common.reaction import Reaction
from SBMLLint.games.som import SOM
from SBMLLint.common.simple_sbml import SimpleSBML

import collections
import itertools
import networkx as nx
import numpy as np
import pandas as pd
import scipy

# BIOMD 383 will test the validity of Gaussian Elimination to find errors, 
# before proceeding to building MESGraph

    
ReactionSummary = collections.namedtuple('ReactionSummary', 
                  'label reactants products category')
REACTION_REDUNDANT = "reaction_redundant"
REACTION_ERROR = "reaction_error"
REACTION_SUMMARY_CATEGORIES = [
    cn.ReactionCategory(category=REACTION_REDUNDANT,
        predicate=lambda x,y,r,p: (x==0) and (y==0)),
    cn.ReactionCategory(category=REACTION_ERROR,
        predicate=lambda x,y,r,p: ((x==0) and (y!=0)) \
                                  or ((x!=0) and (y==0))),
    cn.ReactionCategory(category=cn.REACTION_1_1,
        predicate=lambda x,y,r,p: (x==1) and (y==1) and (sum(r)==sum(p))),
    
    cn.ReactionCategory(category=cn.REACTION_1_n,
        predicate=lambda x,y,r,p: (x==1) and (sum([r[0]<=e for e in p])==len(p))  ),
                     
    cn.ReactionCategory(category=cn.REACTION_n_1,
        predicate=lambda x,y,r,p: (y==1) and (sum([p[0]<=e for e in r])==len(r))),
    ]


class Message(nx.DiGraph):
  """
  Similar to MESGraph, The Message algorithm creates
  a directed graph of SOMs, but before creating the graph
  it creates a row-reduced echolon metrix using the 
  stoichiometry matrix. 
  Type I Error occurs when we find inequality between two molecules
  in the same SOM, because each element in a SOM has the same weight.
  Type II Error implies there is cyclism between molecules, such as
  A < B < C < ... < A, which is physically impossible.
  """
  def __init__(self, simple=None):
    """
    :param SimpleSBML simple:
    """
    self.simple = simple
    self.reactions = self._getNonBoundaryReactions(simple)
    self.molecules = self._getNonBoundaryMolecules(simple, self.reactions)
    self.stoichiometry_matrix = self.getStoichiometryMatrix(self.reactions, self.molecules)
    self.reduced_reactions = []

  def _getNonBoundaryReactions(self, simple):
    """
    Get list of non-boundary reacetions
    :param SimpleSBML simple:
    :return list-Reaction:
    """
    reactions = []
    for reaction in simple.reactions:
      if reaction.category != cn.REACTION_BOUNDARY:
        reactions.append(reaction)
    return reactions  
  #
  def _getNonBoundaryMolecules(self, simple, reactions):
    """
    Get list of non-boundary molecules
    :param SimpleSBML simple:
    :return list-Molecule.name:
    """
    molecules = set()
    for reaction in reactions:
      reactants = {r.molecule.name for r in reaction.reactants}
      products = {r.molecule.name for r in reaction.products}
      molecules = molecules.union(reactants)
      molecules = molecules.union(products)
    return list(molecules)
  #
  def getStoichiometryMatrix(self, reactions, molecules):
    """
    Creates a full stoichiometry matrix
    using non-boundary reactions.
    Helped by https://gist.github.com/lukauskas/d1e30bdccc5b801d341d
    :return pd.DataFrame:
    """
    reaction_labels = [r.label for r in reactions]
    stoichiometry_matrix = pd.DataFrame(0.0, index=molecules, columns=reaction_labels)
    for reaction in reactions:
      reactants = {r.molecule.name:r.stoichiometry for r in reaction.reactants}
      products = {p.molecule.name:p.stoichiometry for p in reaction.products}
      reaction_molecules = list(set(reactants.keys()).union(products.keys()))
      for molecule_name in reaction_molecules:
        net_stoichiometry = products.get(molecule_name, 0.0) - reactants.get(molecule_name, 0.0)
        stoichiometry_matrix[reaction.label][molecule_name] = net_stoichiometry
    return stoichiometry_matrix
  #
  def decomposeMatrix(self, mat_df):
    """
    LU decomposition of the matrix.
    First it transposes the input matrix
    and find P, L, U matrices. 
    :param pandas.DataFrame mat_df:
    :yield typle-numpy.array:
    """
    mat_t = mat_df.T
    idx_mat_t = mat_t.index
    # LU decomposition
    mat_lu = scipy.linalg.lu(mat_t)
    # inverse pivot matrix
    p_inv = scipy.linalg.inv(mat_lu[0])
    pivot_index = [list(k).index(1) for k in p_inv]
    new_idx_mat_t = [idx_mat_t[idx] for idx in pivot_index]
    # row reduced matrix
    row_reduced = pd.DataFrame(mat_lu[2], index=new_idx_mat_t, columns=mat_t.columns).T
    # 'L' matrix
    yield mat_lu[1]
    yield row_reduced
  #
  def getReactionSummaryCategory(self, reactants, products):
    """
    Return category of reaction. Return reaction_n_n
    if none of the above applies
    :param list-MoleculeStoichiometry reactants:
    :param list-Moleculestoichiometry products:
    :return str reaction_category:
    """
    num_reactants = len([r.molecule for r in reactants \
                         if r.molecule.name!=cn.EMPTYSET])
    num_products = len([p.molecule for p in products \
                        if p.molecule.name!=cn.EMPTYSET])
    stoichiometry_reactants = [r.stoichiometry for r \
                                  in reactants \
                                  if r.molecule.name!=cn.EMPTYSET]
    stoichiometry_products = [p.stoichiometry for p \
                             in products \
                             if p.molecule.name!=cn.EMPTYSET]
    for reaction_category in REACTION_SUMMARY_CATEGORIES:
      if reaction_category.predicate(num_reactants, num_products, 
                                     stoichiometry_reactants, 
                                     stoichiometry_products):
        return reaction_category.category
    # if none of the above, return reaction_n_n
    return cn.REACTION_n_n
  #
  def convertMatrixToReactions(self, simple, mat_df):
    """
    Convert a stoichiometry matrix, 
    where columns are reactions and 
    rows are molecules(species),
    to simpleSBML reactions. 
    :param simpleSBML simple:
    :param pandas.DataFrame mat_df:
    :return list-ReactionSummary reactions:
    """
    reactions = []
    for reaction_name in mat_df.columns:
      reaction = simple.getReaction(reaction_name)
      reduced_reaction_series = mat_df[reaction_name]
      reactants = [MoleculeStoichiometry(simple.getMolecule(molecule), 
                                     abs(reduced_reaction_series[molecule])) \
              for molecule in reduced_reaction_series.index if reduced_reaction_series[molecule]<0]
      products = [MoleculeStoichiometry(simple.getMolecule(molecule), 
                                     reduced_reaction_series[molecule]) \
              for molecule in reduced_reaction_series.index if reduced_reaction_series[molecule]>0]
      reactions.append(ReactionSummary(label=reaction_name, 
                                      reactants=reactants,
                                      products=products,
                                      category=getReactionSummaryCategory(reactants, products)))
    return reactions














