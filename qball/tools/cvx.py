
import numpy as np
import cvxpy as cvx
from opymize.linear.diff import staggered_diff_avgskips, imagedim_skips

def cvxVariable(*args):
    """ Create a multidimensional CVXPY variable

    Args:
        args : list of integers (dimensions)
    Returns:
        A multidimensional variable (using python lists if more than two
        dimensions are needed)
    """
    if len(args) <= 2:
        return cvx.Variable(args)
    else:
        return list([cvxVariable(*args[1:]) for i in range(args[0])])

def sparse_div_op(dims):
    """ Sparse linear operator for divergence with dirichlet boundary

    Args:
        dims : dimensions of the image domain
    Returns:
        Sparse linear operator (can be used with cvxOp)
    """
    d_image = len(dims)
    n_image = np.prod(dims)
    avgskips = staggered_diff_avgskips(dims)
    skips = imagedim_skips(dims)
    navgskips =  1 << (d_image - 1)
    coords = np.zeros(d_image, dtype=np.int64)

    op = [[] for i in range(n_image)]
    for t in range(d_image):
        coords.fill(0)
        for i in range(n_image):
            # ignore boundary points
            in_range = True
            for dc in range(d_image-1,-1,-1):
                if coords[dc] >= dims[dc] - 1:
                    in_range = False
                    break

            if in_range:
                for avgskip in avgskips[t]:
                    base = i + avgskip
                    op[base + skips[t]].append(((t,i), -1.0/navgskips))
                    op[base].append(((t,i), 1.0/navgskips))

            # advance coordinates
            for dd in range(d_image-1,-1,-1):
                coords[dd] += 1
                if coords[dd] >= dims[dd]:
                    coords[dd] = 0
                else:
                    break
    return op

def cvxOp(A, x, i):
    """ CVXPY expression for the application of A to x, evaluated at i

    Args:
        A : sparse representation of a linear operator
        x : variable whose size matches the requirements of A
        i : point at which to evaluate
    Returns:
        CVXPY expression for the application of A to x, evaluated at i
    """
    return sum([fact*x[coord] for coord,fact in A[i]])
