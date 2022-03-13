from sympy import Symbol, Eq, Ge, Abs, Function, Number, sin, cos
from modulus.pdes import PDES
from modulus.variables import Variables
import time
from modulus.solver import Solver
from modulus.dataset import TrainDomain, ValidationDomain
from modulus.data import Validation
from modulus.sympy_utils.functions import parabola
from modulus.sympy_utils.geometry_2d import Rectangle, Line
from modulus.csv_utils.csv_rw import csv_to_dict
from modulus.PDES.navier_stokes import NavierStokes, IntegralContinuity
from modulus.controller import ModulusController
import numpy as np
import math
import sys

def get_angle(theta, magnitude):
    return math.cos(theta)*magnitude, math.sin(theta)*magnitude

class Poisson_2D(PDES):
    name = 'Poisson_2D'
    def __init__(self):
        # coordinates
        x, y = Symbol('x'), Symbol('y')

        # angle of attack
        alpha = Symbol('alpha')

        # make input variables
        input_variables = {'x': x, 'y': y, 'alpha': alpha}

        # potential
        phi = Function('phi')(*input_variables)
        
        self.equations = Variables()
        # Here I implement a simpler form of a 2D Navier-Stokes equation in the form of laplacian(u,v) = 0 such that
        # laplacian(u,v).diff(u) = u and laplacian(u,v).diff(v) = v
        # laplacian(u,v).diff(t) = 0
        self.equations['u'] = phi.diff(x) 
        self.equations['v'] = phi.diff(y)
        self.equations['Poisson_2D'] = (phi.diff(x)).diff(x) + (phi.diff(y)).diff(y) # grad^2(phi)


# params for domain
obstacle_length = 0.10
height = 6*obstacle_length  
width = 6*obstacle_length

# define geometry
rec = Rectangle((-width / 2, -height / 2), (width / 2, height / 2))
obstacle = Line((0, 0), (0, obstacle_length), 1)
wake = Line((0, -3*obstacle_length), (0, 0), 1) # Wake to enforce kutta condition

obstacle.rotate(np.pi / 2)
wake.rotate(np.pi / 2)
# I rotate the line by 90 degrees to make it horizontal. 
# Now, the way this system is set up, the line will be positioned such that it is two units from the left of the rectangle, and 3 units 
# from its trailing edge. 

geo = rec

# define sympy varaibles to parametize domain curves
x, y, alpha = Symbol('x'), Symbol('y'), Symbol('alpha')
# limit the range of alpha from -10 to 10 using np.pi.
param_ranges = {alpha, (-np.pi*10/180, np.pi*10/180)}
fixed_param_range = {alpha: lambda batch_size: np.full((batch_size, 1), np.random.uniform(-np.pi*10/180, np.pi*10/180))}

class PotentialTrain(TrainDomain):
    def __init__(self, **config):
        super(PotentialTrain, self).__init__()

#############################################################################################
        # I want to make the inlet velocity to be 10.0 m/s with an incidence angle of 4 degrees at the obstacle.
        # the inverse tan(v/u) gives me the required angle of incidence.
        # Drawing the scenario in comments below:
        #      +---------+
        #     /|/     \|/|
        #    //|// --- //|
        #   ///|/////////|
        #  ////+---------+
        #  //////////////
        #  / ////////////
        #    / //////////
        # where / is u + v such that tan-1(v/u) = x degrees(here i kept x as 4).
        u_x = 10*cos(alpha)
        u_y = 10*sin(alpha)
        flow_rate = u_x*width + u_y*height

        # inlet
        inletBC = geo.boundary_bc(
            outvar_sympy={"u": u_x, "v": u_y},
            batch_size_per_area=250,
            criteria=Eq(x, -width / 2),
            param_ranges ={**fixed_param_range},
            fixed_var=False
        )
        self.add(inletBC, name="Inlet")

        # outlet
        outletBC = geo.boundary_bc(
            outvar_sympy={"u":u_x , "v": u_y}, # Mimicing the far field conditions
            batch_size_per_area=500,
            criteria=Ge(y/height+x/width, 1/2),
            param_ranges ={**fixed_param_range},
            fixed_var=False
        )
        self.add(outletBC, name="Outlet")

        # bottomWall
        bottomWall = geo.boundary_bc(
            outvar_sympy={"u": u_x, "v": u_y},
            batch_size_per_area=250,
            criteria=Eq(y, -height / 2),
            param_ranges ={**fixed_param_range},
            fixed_var=False            
        )
        self.add(bottomWall, name="BottomWall")

        # obstacleLine
        obstacleLine = obstacle.boundary_bc(
            outvar_sympy={"u": u_x, "v": 0},
            batch_size_per_area=500,
            lambda_sympy={"lambda_u": 100, "lambda_v": 100},
            param_ranges ={**fixed_param_range},
            fixed_var=False            
        )
        self.add(obstacleLine, name="obstacleLine")

        # wakeLine
        # Here we define u = u and v = 0 at the trailing edge of the obstacle(which is at x=0, and v = v at x = right wall).
        l = lambda x : (x)/(3*obstacle_length) # x = 0 at the trailing edge of the obstacle
        wakeLine = wake.boundary_bc(
            outvar_sympy={"u": u_x, "v": u_y*l(x)},
            batch_size_per_area=500,
            lambda_sympy={"lambda_u": 100, "lambda_v": 100, },
            param_ranges ={**fixed_param_range},
            fixed_var=False            
        )
        self.add(wakeLine, name="wakeLine")

        # interior
        interior = geo.interior_bc(
            outvar_sympy={"Poisson_2D": 0},
            bounds={x: (-width / 2, width / 2), y: (-height / 2, height / 2)},
            lambda_sympy={
                "lambda_Poisson_2D": geo.sdf,
            },
            batch_size_per_area=2000,
            param_ranges ={**fixed_param_range},
            fixed_var=False            
        )
        self.add(interior, name="Interior")

        neighbourhood = geo.interior_bc(
            outvar_sympy={"Poisson_2D": 0},
            bounds={x: (-height / 3, height / 3), y: (-height / 8, height / 8)},
            lambda_sympy={
                "lambda_Poisson_2D": geo.sdf,
            },
            batch_size_per_area=2000,
            param_ranges ={**fixed_param_range},
            fixed_var=False            
        )
        self.add(neighbourhood, name="Neighbourhood")

class PotentialInference(InferenceDomain)；
    def __init__(self, **config):
        super(PotentialInference, self).__init__()
        interior = Inference(geo.sample_interior(1e6, bounds={x: (-width / 2, width / 2), y: (-height / 2, height / 2)}, ['u','v','phi'])
        self.add(interior, name="Inference")

class PotentialSolver(Solver):
    train_domain = PotentialTrain
    inference_domain=PotentialInference

    def __init__(self, **config):
        super(LDCSolver, self).__init__(**config)
        self.equations = (
            Poisson_2D().make_node()
        )
        flow_net = self.arch.make_node(
            name="flow_net", inputs=["x", "y", "alpha"], outputs=["phi"]
        )
        self.nets = [flow_net]

    @classmethod
    def update_defaults(cls, defaults):
        defaults.update(
            {
                "network_dir": "./network_checkpoint_potential_flow_2d",
                "decay_steps": 4000,
                "max_steps": 400000,
                "layer_size": 100,
                "nr_layer": 2,
            }
        )

if __name__ == "__main__":
    ctr = ModulusController(PotentialSolver)
    ctr.run()
