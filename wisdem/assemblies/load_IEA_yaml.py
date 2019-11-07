import ruamel_yaml as ry
import numpy as np
import jsonschema as json
import time
from scipy.interpolate import PchipInterpolator, interp1d
from openmdao.api import ExplicitComponent, Group, IndepVarComp, Problem
from wisdem.rotorse.geometry_tools.geometry import AirfoilShape
from wisdem.rotorse.rotor_geometry_yaml import arc_length, trailing_edge_smoothing, remap2grid

#######
def calc_axis_intersection(xy_coord, rotation, offset, p_le_d, side, thk=0.):
    # dimentional analysis that takes a rotation and offset from the pitch axis and calculates the airfoil intersection
    # rotation
    offset_x   = offset*np.cos(rotation) + p_le_d[0]
    offset_y   = offset*np.sin(rotation) + p_le_d[1]

    m_rot      = np.sin(rotation)/np.cos(rotation)       # slope of rotated axis
    plane_rot  = [m_rot, -1*m_rot*p_le_d[0]+ p_le_d[1]]  # coefficients for rotated axis line: a1*x + a0

    m_intersection     = np.sin(rotation+np.pi/2.)/np.cos(rotation+np.pi/2.)   # slope perpendicular to rotated axis
    plane_intersection = [m_intersection, -1*m_intersection*offset_x+offset_y] # coefficients for line perpendicular to rotated axis line at the offset: a1*x + a0
    
    # intersection between airfoil surface and the line perpendicular to the rotated/offset axis
    y_intersection = np.polyval(plane_intersection, xy_coord[:,0])
    
    idx_le = np.argmin(xy_coord[:,0])
    xy_coord_arc = arc_length(xy_coord[:,0], xy_coord[:,1])
    arc_L = xy_coord_arc[-1]
    xy_coord_arc /= arc_L
    
    try:
        idx_inter      = np.argwhere(np.diff(np.sign(xy_coord[:,1] - y_intersection))).flatten() # find closest airfoil surface points to intersection 
    except:
        for xi,yi in zip(xy_coord[:,0], xy_coord[:,1]):
            print(xi, yi)
        import matplotlib.pyplot as plt
        plt.plot(xy_coord[:,0], xy_coord[:,1])
        plt.plot(xy_coord[:,0], y_intersection)
        plt.show()
        idx_inter      = np.argwhere(np.diff(np.sign(xy_coord[:,1] - y_intersection))).flatten() # find closest airfoil surface points to intersection 
    

    midpoint_arc = []
    for sidei in side:
        if sidei.lower() == 'suction':
            tangent_line = np.polyfit(xy_coord[idx_inter[0]:idx_inter[0]+2, 0], xy_coord[idx_inter[0]:idx_inter[0]+2, 1], 1)
        elif sidei.lower() == 'pressure':
            tangent_line = np.polyfit(xy_coord[idx_inter[1]:idx_inter[1]+2, 0], xy_coord[idx_inter[1]:idx_inter[1]+2, 1], 1)

        midpoint_x = (tangent_line[1]-plane_intersection[1])/(plane_intersection[0]-tangent_line[0])

        midpoint_y = plane_intersection[0]*(tangent_line[1]-plane_intersection[1])/(plane_intersection[0]-tangent_line[0]) + plane_intersection[1]

        # convert to arc position
        if sidei.lower() == 'suction':
            x_half = xy_coord[:idx_le+1,0]
            arc_half = xy_coord_arc[:idx_le+1]
            
            
            
        elif sidei.lower() == 'pressure':
            x_half = xy_coord[idx_le:,0]
            arc_half = xy_coord_arc[idx_le:]
        
        
        
        midpoint_arc.append(remap2grid(x_half, arc_half, midpoint_x, spline=interp1d))
        # print(xy_coord)
        # print(arc_half)
        # , midpoint_x)
        # print(xy_coord_arc)
        # exit()
    # if len(idx_inter) == 0:
    # print(blade['pf']['s'][i], blade['pf']['r'][i], blade['pf']['chord'][i], thk)
    # import matplotlib.pyplot as plt
    # plt.plot(xy_coord[:,0], xy_coord[:,1])
    # plt.axis('equal')
    # ymin, ymax = plt.gca().get_ylim()
    # xmin, xmax = plt.gca().get_xlim()
    # plt.plot(xy_coord[:,0], y_intersection)
    # plt.plot(p_le_d[0], p_le_d[1], '.')
    # plt.axis([xmin, xmax, ymin, ymax])
    # plt.show()

    return midpoint_arc


class WT_Data(object):
    # Pure python class to load the input yaml file and break into few sub-dictionaries, namely:
    #   - wt_init_options: dictionary with all the inputs that will be passed as options to the openmdao components, such as the length of the arrays
    #   - blade: dictionary representing the entry blade in the yaml file
    #   - tower: dictionary representing the entry tower in the yaml file
    #   - nacelle: dictionary representing the entry nacelle in the yaml file
    #   - materials: dictionary representing the entry materials in the yaml file
    #   - airfoils: dictionary representing the entry airfoils in the yaml file

    def __init__(self):

        # Validate input file against JSON schema
        self.validate        = True        # (bool) run IEA turbine ontology JSON validation
        self.fname_schema    = ''          # IEA turbine ontology JSON schema file

        self.verbose         = False
        
        self.n_aoa           = 200         # Number of angles of attack used to define polars
        self.n_xy            = 200         # Number of angles of coordinate points used to discretize each airfoil
        self.n_span          = 30          # Number of spanwise stations used to define the blade properties
        

    def initialize(self, fname_input):
        # Class instance to break the yaml into sub dictionaries
        if self.verbose:
            print('Running initialization: %s' % fname_input)

        # Load input
        self.fname_input = fname_input
        self.wt_ref = self.load_ontology(self.fname_input, validate=self.validate, fname_schema=self.fname_schema)

        wt_init_options = self.openmdao_vectors()        
        blade           = self.wt_ref['components']['blade']
        tower           = {} # self.wt_ref['components']['tower']
        nacelle         = {} # self.wt_ref['components']['tower']
        materials       = self.wt_ref['materials']
        airfoils        = self.wt_ref['airfoils']
        

        return wt_init_options, blade, tower, nacelle, materials, airfoils

    def openmdao_vectors(self):
        # Class instance to determine all the parameters used to initialize the openmdao arrays, i.e. number of airfoils, number of angles of attack, number of blade spanwise stations, etc
        wt_init_options = {}
        
        # Materials
        wt_init_options['materials']          = {}
        wt_init_options['materials']['n_mat'] = len(self.wt_ref['materials'])
        
        # Airfoils
        wt_init_options['airfoils']           = {}
        wt_init_options['airfoils']['n_af']   = len(self.wt_ref['airfoils'])
        wt_init_options['airfoils']['n_aoa']  = self.n_aoa
        wt_init_options['airfoils']['aoa']    = np.unique(np.hstack([np.linspace(-180., -30., wt_init_options['airfoils']['n_aoa'] / 4. + 1), np.linspace(-30., 30., wt_init_options['airfoils']['n_aoa'] / 2.), np.linspace(30., 180., wt_init_options['airfoils']['n_aoa'] / 4. + 1)]))
        
        Re_all = []
        for i in range(wt_init_options['airfoils']['n_af']):
            for j in range(len(self.wt_ref['airfoils'][i]['polars'])):
                Re_all.append(self.wt_ref['airfoils'][i]['polars'][j]['re'])
        wt_init_options['airfoils']['n_Re']   = len(np.unique(Re_all))
        wt_init_options['airfoils']['n_tab']  = 1
        wt_init_options['airfoils']['n_xy']   = self.n_xy
        
        # Blade
        wt_init_options['blade']              = {}
        wt_init_options['blade']['n_span']    = self.n_span
        wt_init_options['blade']['nd_span']   = np.linspace(0., 1., wt_init_options['blade']['n_span']) # Equally spaced non-dimensional spanwise grid
        wt_init_options['blade']['n_af_span'] = len(self.wt_ref['components']['blade']['outer_shape_bem']['airfoil_position']['labels']) # This is the number of airfoils defined along blade span and it is often different than n_af, which is the number of airfoils defined in the airfoil database
        wt_init_options['blade']['n_webs']    = len(self.wt_ref['components']['blade']['internal_structure_2d_fem']['webs'])
        wt_init_options['blade']['n_layers']  = len(self.wt_ref['components']['blade']['internal_structure_2d_fem']['layers'])
        
        
        return wt_init_options

    def load_ontology(self, fname_input, validate=False, fname_schema=''):
        """ Load inputs IEA turbine ontology yaml inputs, optional validation """
        # Read IEA turbine ontology yaml input file
        t_load = time.time()
        with open(fname_input, 'r') as myfile:
            inputs = myfile.read()

        # Validate the turbine input with the IEA turbine ontology schema
        yaml = ry.YAML()
        if validate:
            t_validate = time.time()

            with open(fname_schema, 'r') as myfile:
                schema = myfile.read()
            json.validate(yaml.load(inputs), yaml.load(schema))

            t_validate = time.time()-t_validate
            if self.verbose:
                print('Complete: Schema "%s" validation: \t%f s'%(fname_schema, t_validate))
        else:
            t_validate = 0.

        if self.verbose:
            t_load = time.time() - t_load - t_validate
            print('Complete: Load Input File: \t%f s'%(t_load))
        
        return yaml.load(inputs)

class Blade(Group):
    # Openmdao group with components with the blade data coming from the input yaml file.
    def initialize(self):
        self.options.declare('blade_init_options')
        self.options.declare('af_init_options')
                
    def setup(self):
        blade_init_options = self.options['blade_init_options']
        af_init_options    = self.options['af_init_options']
        self.add_subsystem('outer_shape_bem', Blade_Outer_Shape_BEM(blade_init_options = blade_init_options), promotes = ['length'])
        self.add_subsystem('interp_airfoils', Blade_Interp_Airfoils(blade_init_options = blade_init_options, af_init_options = af_init_options))
        self.add_subsystem('internal_structure_2d_fem', Blade_Internal_Structure_2D_FEM(blade_init_options = blade_init_options, af_init_options = af_init_options))
        
        self.connect('outer_shape_bem.s',           'interp_airfoils.s')
        self.connect('outer_shape_bem.chord',       'interp_airfoils.chord')
        self.connect('outer_shape_bem.pitch_axis',  'interp_airfoils.pitch_axis')
        self.connect('outer_shape_bem.af_used',     'interp_airfoils.af_used')
        self.connect('outer_shape_bem.af_position', 'interp_airfoils.af_position')
        
        self.connect('outer_shape_bem.twist',           'internal_structure_2d_fem.twist')
        self.connect('interp_airfoils.coord_xy_dim',    'internal_structure_2d_fem.coord_xy_dim')
   
class Blade_Outer_Shape_BEM(ExplicitComponent):
    # Openmdao component with the blade outer shape data coming from the input yaml file.
    def initialize(self):
        self.options.declare('blade_init_options')
        
    def setup(self):
        blade_init_options = self.options['blade_init_options']
        n_af_span          = blade_init_options['n_af_span']
        n_span             = blade_init_options['n_span']
        
        self.add_discrete_output('af_used', val=n_af_span * [''],              desc='1D array of names of the airfoils actually defined along blade span.')
        
        self.add_output('af_position',   val=np.zeros(n_af_span),              desc='1D array of the non dimensional positions of the airfoils af_used defined along blade span.')
        self.add_output('s',             val=np.zeros(n_span),                 desc='1D array of the non-dimensional spanwise grid defined along blade axis (0-blade root, 1-blade tip)')
        self.add_output('chord',         val=np.zeros(n_span),    units='m',   desc='1D array of the chord values defined along blade span.')
        self.add_output('twist',         val=np.zeros(n_span),    units='deg', desc='1D array of the twist values defined along blade span. The twist is defined positive for negative rotations around the z axis (the same as in BeamDyn).')
        self.add_output('pitch_axis',    val=np.zeros(n_span),                 desc='1D array of the chordwise position of the pitch axis (0-LE, 1-TE), defined along blade span.')
        self.add_output('ref_axis',      val=np.zeros((n_span,3)),units='m',   desc='2D array of the coordinates (x,y,z) of the blade reference axis, defined along blade span. The coordinate system is the one of BeamDyn: it is placed at blade root with x pointing the suction side of the blade, y pointing the trailing edge and z along the blade span. A standard configuration will have negative x values (prebend), if swept positive y values, and positive z values.')

        self.add_output('length',       val = 0.0,               units='m',    desc='Scalar of the 3D blade length computed along its axis.')
        self.add_output('length_z',     val = 0.0,               units='m',    desc='Scalar of the 1D blade length along z, i.e. the blade projection in the plane ignoring prebend and sweep. For a straight blade this is equal to length')
        
    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        
        outputs['length']   = arc_length(outputs['ref_axis'][:,0], outputs['ref_axis'][:,1], outputs['ref_axis'][:,2])[-1]
        outputs['length_z'] = outputs['ref_axis'][:,2][-1]

class Blade_Interp_Airfoils(ExplicitComponent):
    # Openmdao component to interpolate airfoil coordinates and airfoil polars along the span of the blade for a predefined set of airfoils coming from component Airfoils.
    def initialize(self):
        self.options.declare('blade_init_options')
        self.options.declare('af_init_options')
        
    def setup(self):
        blade_init_options = self.options['blade_init_options']
        self.n_af_span     = n_af_span = blade_init_options['n_af_span']
        self.n_span        = n_span    = blade_init_options['n_span']
        af_init_options    = self.options['af_init_options']
        self.n_af          = n_af      = af_init_options['n_af'] # Number of airfoils
        self.n_aoa         = n_aoa     = af_init_options['n_aoa']# Number of angle of attacks
        self.n_Re          = n_Re      = af_init_options['n_Re'] # Number of Reynolds, so far hard set at 1
        self.n_tab         = n_tab     = af_init_options['n_tab']# Number of tabulated data. For distributed aerodynamic control this could be > 1
        self.n_xy          = n_xy      = af_init_options['n_xy'] # Number of coordinate points to describe the airfoil geometry
        
        self.add_discrete_input('af_used', val=n_af_span * [''],              desc='1D array of names of the airfoils defined along blade span.')
        
        self.add_input('af_position',   val=np.zeros(n_af_span),              desc='1D array of the non dimensional positions of the airfoils af_used defined along blade span.')
        self.add_input('s',             val=np.zeros(n_span),                 desc='1D array of the non-dimensional spanwise grid defined along blade axis (0-blade root, 1-blade tip)')
        self.add_input('pitch_axis',    val=np.zeros(n_span),                 desc='1D array of the chordwise position of the pitch axis (0-LE, 1-TE), defined along blade span.')
        self.add_input('chord',         val=np.zeros(n_span),    units='m',   desc='1D array of the chord values defined along blade span.')
        
        # Airfoil properties
        self.add_discrete_input('name', val=n_af * [''],                        desc='1D array of names of airfoils.')
        self.add_input('ac',        val=np.zeros(n_af),                         desc='1D array of the aerodynamic centers of each airfoil.')
        self.add_input('r_thick',   val=np.zeros(n_af),                         desc='1D array of the relative thicknesses of each airfoil.')
        self.add_input('aoa',       val=np.zeros(n_aoa),        units='deg',    desc='1D array of the angles of attack used to define the polars of the airfoils. All airfoils defined in openmdao share this grid.')
        self.add_input('cl',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the lift coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_input('cd',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the drag coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_input('cm',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the moment coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        
        # Airfoil coordinates
        self.add_input('coord_xy',  val=np.zeros((n_af, n_xy, 2)),              desc='3D array of the x and y airfoil coordinates of the n_af airfoils.')
        
        # Polars and coordinates interpolated along span
        self.add_output('r_thick_interp',   val=np.zeros(n_span),                         desc='1D array of the relative thicknesses of the blade defined along span.')
        self.add_output('ac_interp',        val=np.zeros(n_span),                         desc='1D array of the aerodynamic center of the blade defined along span.')
        self.add_output('cl_interp',        val=np.zeros((n_span, n_aoa, n_Re, n_tab)),   desc='4D array with the lift coefficients of the airfoils. Dimension 0 is along the blade span for n_span stations, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_output('cd_interp',        val=np.zeros((n_span, n_aoa, n_Re, n_tab)),   desc='4D array with the drag coefficients of the airfoils. Dimension 0 is along the blade span for n_span stations, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_output('cm_interp',        val=np.zeros((n_span, n_aoa, n_Re, n_tab)),   desc='4D array with the moment coefficients of the airfoils. Dimension 0 is along the blade span for n_span stations, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_output('coord_xy_interp',  val=np.zeros((n_span, n_xy, 2)),              desc='3D array of the non-dimensional x and y airfoil coordinates of the airfoils interpolated along span for n_span stations.')
        self.add_output('coord_xy_dim',     val=np.zeros((n_span, n_xy, 2)), units = 'm', desc='3D array of the dimensional x and y airfoil coordinates of the airfoils interpolated along span for n_span stations. The origin is placed at the pitch axis.')
        
    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        
        # Reconstruct the blade relative thickness along span with a pchip
        r_thick_used    = np.zeros(self.n_af_span)
        coord_xy_used   = np.zeros((self.n_af_span, self.n_xy, 2))
        coord_xy_interp = np.zeros((self.n_span, self.n_xy, 2))
        coord_xy_dim    = np.zeros((self.n_span, self.n_xy, 2))
        cl_used         = np.zeros((self.n_af_span, self.n_aoa, self.n_Re, self.n_tab))
        cl_interp       = np.zeros((self.n_span, self.n_aoa, self.n_Re, self.n_tab))
        cd_used         = np.zeros((self.n_af_span, self.n_aoa, self.n_Re, self.n_tab))
        cd_interp       = np.zeros((self.n_span, self.n_aoa, self.n_Re, self.n_tab))
        cm_used         = np.zeros((self.n_af_span, self.n_aoa, self.n_Re, self.n_tab))
        cm_interp       = np.zeros((self.n_span, self.n_aoa, self.n_Re, self.n_tab))
        
        for i in range(self.n_af_span):
            for j in range(self.n_af):
                if discrete_inputs['af_used'][i] == discrete_inputs['name'][j]:                    
                    r_thick_used[i]     = inputs['r_thick'][j]
                    coord_xy_used[i,:,:]= inputs['coord_xy'][j]
                    cl_used[i,:,:,:]    = inputs['cl'][j,:,:,:]
                    cd_used[i,:,:,:]    = inputs['cd'][j,:,:,:]
                    cm_used[i,:,:,:]    = inputs['cm'][j,:,:,:]
                    break
        
        spline         = PchipInterpolator
        rthick_spline  = spline(inputs['af_position'], r_thick_used)
        outputs['r_thick_interp'] = rthick_spline(inputs['s'])
        
        # Spanwise interpolation of the profile coordinates with a pchip
        r_thick_unique, indices  = np.unique(r_thick_used, return_index = True)
        profile_spline  = spline(r_thick_unique, coord_xy_used[indices, :, :])        
        coord_xy_interp = np.flip(profile_spline(np.flip(outputs['r_thick_interp'])), axis=0)
        
        
        for i in range(self.n_span):
            af_le = coord_xy_interp[i, np.argmin(coord_xy_interp[i,:,0]),:]
            coord_xy_interp[i,:,0] -= af_le[0]
            coord_xy_interp[i,:,1] -= af_le[1]
            c = max(coord_xy_interp[i,:,0]) - min(coord_xy_interp[i,:,0])
            coord_xy_interp[i,:,:] /= c
            # If the rel thickness is smaller than 0.4 apply a trailing ege smoothing step
            if outputs['r_thick_interp'][i] < 0.4: 
                coord_xy_interp[i,:,:] = trailing_edge_smoothing(coord_xy_interp[i,:,:])
            
        pitch_axis = inputs['pitch_axis']
        chord      = inputs['chord']

        
        coord_xy_dim = coord_xy_interp
        coord_xy_dim[:,:,0] -= pitch_axis[:, np.newaxis]
        coord_xy_dim = coord_xy_dim*chord[:, np.newaxis, np.newaxis]
                
        
        # Spanwise interpolation of the airfoil polars with a pchip
        cl_spline = spline(r_thick_unique, cl_used[indices, :, :, :])        
        cl_interp = np.flip(cl_spline(np.flip(outputs['r_thick_interp'])), axis=0)
        cd_spline = spline(r_thick_unique, cd_used[indices, :, :, :])        
        cd_interp = np.flip(cd_spline(np.flip(outputs['r_thick_interp'])), axis=0)
        cm_spline = spline(r_thick_unique, cm_used[indices, :, :, :])        
        cm_interp = np.flip(cm_spline(np.flip(outputs['r_thick_interp'])), axis=0)
        
        # Plot interpolated coordinates
        # import matplotlib.pyplot as plt
        # for i in range(self.n_span):    
            # plt.plot(coord_xy_interp[i,:,0], coord_xy_interp[i,:,1], 'k')
            # plt.axis('equal')
            # plt.title(i)
            # plt.show()

        # import matplotlib.pyplot as plt
        # for i in range(self.n_span):    
            # plt.plot(coord_xy_dim[i,:,0], coord_xy_dim[i,:,1], 'k')
            # plt.axis('equal')
            # plt.title(i)
            # plt.show()

        
        # Plot interpolated polars
        # for i in range(self.n_span):    
            # plt.plot(inputs['aoa'], cl_interp[i,:,0,0], 'b')
            # plt.plot(inputs['aoa'], cd_interp[i,:,0,0], 'r')
            # plt.plot(inputs['aoa'], cm_interp[i,:,0,0], 'k')
            # plt.title(i)
            # plt.show()  
            
        outputs['coord_xy_interp'] = coord_xy_interp
        outputs['coord_xy_dim']    = coord_xy_dim
        outputs['cl_interp']       = cl_interp
        outputs['cd_interp']       = cd_interp
        outputs['cm_interp']       = cm_interp
        
class Blade_Internal_Structure_2D_FEM(ExplicitComponent):
    # Openmdao component with the blade internal structure data coming from the input yaml file.
    def initialize(self):
        self.options.declare('blade_init_options')
        self.options.declare('af_init_options')
        
    def setup(self):
        blade_init_options = self.options['blade_init_options']
        af_init_options    = self.options['af_init_options']
        self.n_span        = n_span    = blade_init_options['n_span']
        self.n_webs        = n_webs    = blade_init_options['n_webs']
        self.n_layers      = n_layers  = blade_init_options['n_layers']
        self.n_xy          = n_xy      = af_init_options['n_xy'] # Number of coordinate points to describe the airfoil geometry
        
        
        self.add_input('coord_xy_dim',     val=np.zeros((n_span, n_xy, 2)),units = 'm',  desc='3D array of the dimensional x and y airfoil coordinates of the airfoils interpolated along span for n_span stations. The origin is placed at the pitch axis.')
        self.add_input('twist',            val=np.zeros(n_span),           units='deg',  desc='1D array of the twist values defined along blade span. The twist is defined positive for negative rotations around the z axis (the same as in BeamDyn).')
        
        self.add_discrete_output('web_name', val=n_webs * [''],                          desc='1D array of the names of the shear webs defined in the blade structure.')
        
        self.add_output('s',             val=np.zeros(n_span),                           desc='1D array of the non-dimensional spanwise grid defined along blade axis (0-blade root, 1-blade tip)')
        self.add_output('webs_rotation', val=np.zeros((n_webs, n_span)),   units='deg',  desc='2D array of the rotation angle of the shear webs in respect to the chord line. The first dimension represents each shear web, the second dimension represents each entry along blade span. If the rotation is equal to negative twist +- a constant, then the web is built straight.')
        self.add_output('webs_offset_y_pa',val=np.zeros((n_webs, n_span)), units='m',    desc='2D array of the offset along the y axis to set the position of the shear webs. Positive values move the web towards the trailing edge, negative values towards the leading edge. The first dimension represents each shear web, the second dimension represents each entry along blade span.')
        
        self.add_discrete_output('layer_name', val=n_layers * [''],                      desc='1D array of the names of the layers modeled in the blade structure.')
        self.add_discrete_output('layer_mat',  val=n_layers * [''],                      desc='1D array of the names of the materials of each layer modeled in the blade structure.')
        self.add_discrete_output('layer_web',  val=n_layers * [''],                      desc='1D array of the names of the webs the layer is associated to. If the layer is on the outer profile this entry can simply stay empty.')
        
        self.add_output('thickness',         val=np.zeros((n_layers, n_span)), units='m',      desc='2D array of the thickness of the layers of the blade structure. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        self.add_output('layer_rotation',    val=np.zeros((n_layers, n_span)), units='deg',  desc='2D array of the rotation angle of a layer in respect to the chord line. The first dimension represents each layer, the second dimension represents each entry along blade span. If the rotation is equal to negative twist +- a constant, then the layer is built straight.')
        self.add_output('layer_offset_y_pa', val=np.zeros((n_layers, n_span)), units='m',    desc='2D array of the offset along the y axis to set the position of a layer. Positive values move the layer towards the trailing edge, negative values towards the leading edge. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        self.add_output('layer_width',       val=np.zeros((n_layers, n_span)), units='m',    desc='2D array of the width along the outer profile of a layer. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        self.add_output('layer_midpoint_nd', val=np.zeros((n_layers, n_span)),               desc='2D array of the non-dimensional midpoint defined along the outer profile of a layer. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        self.add_discrete_output('layer_side',  val=n_layers * [''],                         desc='1D array setting whether the layer is on the suction or pressure side. This entry is only used if definition_layer is equal to 1 or 2.')
        self.add_output('layer_start_nd',    val=np.zeros((n_layers, n_span)),               desc='2D array of the non-dimensional start point defined along the outer profile of a layer. The TE suction side is 0, the TE pressure side is 1. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        self.add_output('layer_end_nd',      val=np.zeros((n_layers, n_span)),               desc='2D array of the non-dimensional end point defined along the outer profile of a layer. The TE suction side is 0, the TE pressure side is 1. The first dimension represents each layer, the second dimension represents each entry along blade span.')
        
        self.add_discrete_output('definition_web',   val=np.zeros(n_webs),                   desc='1D array of flags identifying how webs are specified in the yaml. 1) offset+rotation=twist 2) offset+rotation')
        self.add_discrete_output('definition_layer', val=np.zeros(n_layers),                 desc='1D array of flags identifying how layers are specified in the yaml. 1) offset+rotation=twist+width 2) offset+rotation+width')
    
    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        
        webs_rotation   = np.zeros((self.n_webs, self.n_span))
        layer_rotation  = np.zeros((self.n_layers, self.n_span))
        layer_start_nd  = np.zeros((self.n_layers, self.n_span))
        layer_end_nd    = np.zeros((self.n_layers, self.n_span))
                
        for i in range(self.n_webs):
            if discrete_outputs['definition_web'][i] == 1:
                webs_rotation[i,:] = - inputs['twist']

        for i in range(self.n_span):
            xy_coord_i  = inputs['coord_xy_dim'][i,:,:]
            xy_arc_i    = arc_length(xy_coord_i[:,0], xy_coord_i[:,1])
            arc_L_i     = xy_arc_i[-1]
            xy_arc_i    /= arc_L_i
            for j in range(self.n_layers):
                if discrete_outputs['definition_layer'][j] == 1:
                    layer_rotation[j,i] = - inputs['twist'][i]
                    midpoint = calc_axis_intersection(inputs['coord_xy_dim'][i,:,:], layer_rotation[j,i] / 180. * np.pi, outputs['layer_offset_y_pa'][j,i], [0.,0.], [discrete_outputs['layer_side'][j]])[0]
                    width    = outputs['layer_width'][j,i]
                    layer_start_nd[j,i] = midpoint-width/arc_L_i/2.
                    layer_end_nd[j,i]   = midpoint+width/arc_L_i/2.
        
        outputs['webs_rotation']  = webs_rotation
        outputs['layer_rotation'] = layer_rotation
        outputs['layer_start_nd'] = layer_start_nd
        outputs['layer_end_nd']   = layer_end_nd

    
class Materials(ExplicitComponent):
    # Openmdao component with the wind turbine materials coming from the input yaml file. The inputs and outputs are arrays where each entry represents a material
    
    def initialize(self):
        self.options.declare('mat_init_options')
    
    def setup(self):
        
        mat_init_options = self.options['mat_init_options']
        self.n_mat = n_mat = mat_init_options['n_mat']
        
        self.add_discrete_output('name', val=n_mat * [''],                         desc='1D array of names of materials.')
        self.add_discrete_output('orth', val=np.zeros(n_mat),                      desc='1D array of flags to set whether a material is isotropic (0) or orthtropic (1). Each entry represents a material.')
        self.add_discrete_output('component_id', val=np.zeros(n_mat),              desc='1D array of flags to set whether a material is used in a blade: 0 - coating, 1 - sandwich filler , 2 - shell skin, 3 - shear webs, 4 - spar caps, 5 - TE reinf.isotropic.')
        
        self.add_output('E',             val=np.zeros([n_mat, 3]), units='Pa',     desc='2D array of the Youngs moduli of the materials. Each row represents a material, the three columns represent E11, E22 and E33.')
        self.add_output('G',             val=np.zeros([n_mat, 3]), units='Pa',     desc='2D array of the shear moduli of the materials. Each row represents a material, the three columns represent G12, G13 and G23.')
        self.add_output('nu',            val=np.zeros([n_mat, 3]),                 desc='2D array of the Poisson ratio of the materials. Each row represents a material, the three columns represent nu12, nu13 and nu23.')
        self.add_output('rho',           val=np.zeros(n_mat),      units='kg/m**3',desc='1D array of the density of the materials. For composites, this is the density of the laminate.')
        self.add_output('unit_cost',     val=np.zeros(n_mat),      units='USD/kg', desc='1D array of the unit costs of the materials.')
        self.add_output('waste',         val=np.zeros(n_mat),                      desc='1D array of the non-dimensional waste fraction of the materials.')
        self.add_output('rho_fiber',     val=np.zeros(n_mat),      units='kg/m**3',desc='1D array of the density of the fibers of the materials.')
        self.add_output('rho_area_dry',  val=np.zeros(n_mat),      units='kg/m**2',desc='1D array of the dry aerial density of the composite fabrics. Non-composite materials are kept at 0.')
        
        self.add_output('ply_t',        val=np.zeros(n_mat),      units='m',      desc='1D array of the ply thicknesses of the materials. Non-composite materials are kept at 0.')
        self.add_output('fvf',          val=np.zeros(n_mat),                      desc='1D array of the non-dimensional fiber volume fraction of the composite materials. Non-composite materials are kept at 0.')
        self.add_output('fwf',          val=np.zeros(n_mat),                      desc='1D array of the non-dimensional fiber weight- fraction of the composite materials. Non-composite materials are kept at 0.')
        
    def compute(self, inputs, outputs, discrete_inputs, discrete_outputs):
        
        density_resin = 0.
        for i in range(self.n_mat):
            if discrete_outputs['name'][i] == 'resin':
                density_resin = outputs['rho'][i]
                id_resin = i
        if density_resin==0.:
            exit('Error: a material named resin must be defined in the input yaml')
        
        fvf   = np.zeros(self.n_mat)
        fwf   = np.zeros(self.n_mat)
        ply_t = np.zeros(self.n_mat)
        
        for i in range(self.n_mat):
            if discrete_outputs['component_id'][i] > 1: # It's a composite
                # Formula to estimate the fiber volume fraction fvf from the laminate and the fiber densities
                fvf[i]  = (outputs['rho'][i] - density_resin) / (outputs['rho_fiber'][i] - density_resin) 
                if outputs['fvf'][i] > 0.:
                    if abs(fvf[i] - outputs['fvf'][i]) > 1e-3:
                        exit('Error: the fvf of composite ' + discrete_output['name'][i] + ' specified in the yaml is equal to '+ str(outputs['fvf'][i] * 100) + '%, but this value is not compatible to the other values provided. It should instead be equal to ' + str(fvf[i]*100.) + '%')
                else:
                    outputs['fvf'][i] = fvf[i]
                # Formula to estimate the fiber weight fraction fwf from the fiber volume fraction and the fiber densities
                fwf[i]  = outputs['rho_fiber'][i] * outputs['fvf'][i] / (density_resin + ((outputs['rho_fiber'][i] - density_resin) * outputs['fvf'][i]))
                if outputs['fwf'][i] > 0.:
                    if abs(fwf[i] - outputs['fwf'][i]) > 1e-3:
                        exit('Error: the fwf of composite ' + discrete_output['name'][i] + ' specified in the yaml is equal to '+ str(outputs['fwf'][i] * 100) + '%, but this value is not compatible to the other values provided. It should instead be equal to ' + str(fwf[i]*100.) + '%')
                else:
                    outputs['fwf'][i] = fwf[i]
                # Formula to estimate the plyt thickness ply_t of a laminate from the aerial density, the laminate density and the fiber weight fraction
                ply_t[i] = outputs['rho_area_dry'][i] / outputs['rho'][i] / outputs['fwf'][i]
                if outputs['ply_t'][i] > 0.:
                    if abs(ply_t[i] - outputs['ply_t'][i]) > 1e-3:
                        exit('Error: the ply_t of composite ' + discrete_output['name'][i] + ' specified in the yaml is equal to '+ str(outputs['ply_t'][i]) + 'm, but this value is not compatible to the other values provided. It should instead be equal to ' + str(ply_t[i]) + 'm')
                else:
                    outputs['ply_t'][i] = ply_t[i]
        
class Airfoils(ExplicitComponent):
    def initialize(self):
        self.options.declare('af_init_options')
    
    def setup(self):
        af_init_options = self.options['af_init_options']
        n_af            = af_init_options['n_af'] # Number of airfoils
        n_aoa           = af_init_options['n_aoa']# Number of angle of attacks
        n_Re            = af_init_options['n_Re'] # Number of Reynolds, so far hard set at 1
        n_tab           = af_init_options['n_tab']# Number of tabulated data. For distributed aerodynamic control this could be > 1
        n_xy            = af_init_options['n_xy'] # Number of coordinate points to describe the airfoil geometry
        
        # Airfoil properties
        self.add_discrete_output('name', val=n_af * [''],                        desc='1D array of names of airfoils.')
        self.add_output('ac',        val=np.zeros(n_af),                         desc='1D array of the aerodynamic centers of each airfoil.')
        self.add_output('r_thick',   val=np.zeros(n_af),                         desc='1D array of the relative thicknesses of each airfoil.')
        self.add_output('aoa',       val=np.zeros(n_aoa),        units='deg',    desc='1D array of the angles of attack used to define the polars of the airfoils. All airfoils defined in openmdao share this grid.')
        self.add_output('Re',        val=np.zeros(n_Re),                         desc='1D array of the Reynolds numbers used to define the polars of the airfoils. All airfoils defined in openmdao share this grid.')
        self.add_output('tab',       val=np.zeros(n_tab),                        desc='1D array of the values of the "tab" entity used to define the polars of the airfoils. All airfoils defined in openmdao share this grid. The tab could for example represent a flap deflection angle.')
        self.add_output('cl',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the lift coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_output('cd',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the drag coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        self.add_output('cm',        val=np.zeros((n_af, n_aoa, n_Re, n_tab)),   desc='4D array with the moment coefficients of the airfoils. Dimension 0 is along the different airfoils defined in the yaml, dimension 1 is along the angles of attack, dimension 2 is along the Reynolds number, dimension 3 is along the number of tabs, which may describe multiple sets at the same station, for example in presence of a flap.')
        
        # Airfoil coordinates
        self.add_output('coord_xy',  val=np.zeros((n_af, n_xy, 2)),              desc='3D array of the x and y airfoil coordinates of the n_af airfoils.')   
        
class Wind_Turbine(Group):
    # Openmdao group with all wind turbine data
    
    def initialize(self):
        self.options.declare('wt_init_options')
        
    def setup(self):
        wt_init_options = self.options['wt_init_options']
        self.add_subsystem('materials', Materials(mat_init_options = wt_init_options['materials']))
        self.add_subsystem('airfoils',  Airfoils(af_init_options   = wt_init_options['airfoils']))
        self.add_subsystem('blade',     Blade(blade_init_options   = wt_init_options['blade'], af_init_options   = wt_init_options['airfoils']))
        
        self.connect('airfoils.name',    'blade.interp_airfoils.name')
        self.connect('airfoils.r_thick', 'blade.interp_airfoils.r_thick')
        self.connect('airfoils.coord_xy','blade.interp_airfoils.coord_xy')
        self.connect('airfoils.aoa',     'blade.interp_airfoils.aoa')
        self.connect('airfoils.cl',      'blade.interp_airfoils.cl')
        self.connect('airfoils.cd',      'blade.interp_airfoils.cd')
        self.connect('airfoils.cm',      'blade.interp_airfoils.cm')

def yaml2openmdao(wt_opt, wt_init_options, blade, tower, nacelle, materials, airfoils):
    # Function to assign values to the openmdao group Wind_Turbine and all its components
    
    wt_opt = assign_material_values(wt_opt, wt_init_options, materials)
    wt_opt = assign_airfoils_values(wt_opt, wt_init_options, airfoils)
    wt_opt = assign_blade_values(wt_opt, wt_init_options, blade)
    
    return wt_opt
    
def assign_material_values(wt_opt, wt_init_options, materials):
    # Function to assign values to the openmdao component Materials
    
    n_mat = wt_init_options['materials']['n_mat']
    
    name        = n_mat * ['']
    orth        = np.zeros(n_mat)
    component_id= -np.ones(n_mat)
    rho         = np.zeros(n_mat)
    E           = np.zeros([n_mat, 3])
    G           = np.zeros([n_mat, 3])
    nu          = np.zeros([n_mat, 3])
    rho_fiber   = np.zeros(n_mat)
    rho_area_dry= np.zeros(n_mat)
    fvf         = np.zeros(n_mat)
    fvf         = np.zeros(n_mat)
    fwf         = np.zeros(n_mat)
    
    for i in range(n_mat):
        name[i] =  materials[i]['name']
        orth[i] =  materials[i]['orth']
        rho[i]  =  materials[i]['rho']
        if 'component_id' in materials[i]:
            component_id[i] = materials[i]['component_id']
        
        if orth[i] == 0:
            if 'E' in materials[i]:
                E[i,:]  = np.ones(3) * materials[i]['E']
            if 'nu' in materials[i]:
                nu[i,:] = np.ones(3) * materials[i]['nu']
            if 'G' in materials[i]:
                G[i,:]  = np.ones(3) * materials[i]['G']
            elif 'nu' in materials[i]:
                G[i,:]  = np.ones(3) * materials[i]['E']/(2*(1+materials[i]['nu'])) # If G is not provided but the material is isotropic and we have E and nu we can just estimate it
                warning_shear_modulus_isotropic = 'Ontology input warning: No shear modulus, G, provided for material "%s".  Assuming 2G*(1 + nu) = E, which is only valid for isotropic materials.'%mati['name']
                print(warning_shear_modulus_isotropic)
                
        elif orth[i] == 1:
            E[i,:]  = materials[i]['E']
            G[i,:]  = materials[i]['G']
            nu[i,:] = materials[i]['nu']
        else:
            exit('')
        if 'fiber_density' in materials[i]:
            rho_fiber[i]    = materials[i]['fiber_density']
        if 'area_density_dry' in materials[i]:
            rho_area_dry[i] = materials[i]['area_density_dry']
        
        
        if 'fvf' in materials[i]:
            fvf[i] = materials[i]['fvf']
        if 'fwf' in materials[i]:
            fwf[i] = materials[i]['fwf']
            
            
    wt_opt['materials.name']     = name
    wt_opt['materials.orth']     = orth
    wt_opt['materials.rho']      = rho
    wt_opt['materials.component_id']= component_id
    wt_opt['materials.E']        = E
    wt_opt['materials.G']        = G
    wt_opt['materials.nu']       = nu
    wt_opt['materials.rho_fiber']      = rho_fiber
    wt_opt['materials.rho_area_dry']   = rho_area_dry
    wt_opt['materials.fvf']      = fvf
    wt_opt['materials.fwf']      = fwf

    return wt_opt

def assign_airfoils_values(wt_opt, wt_init_options, airfoils):
    # Function to assign values to the openmdao component Airfoils
    
    n_af  = wt_init_options['airfoils']['n_af']
    n_aoa = wt_init_options['airfoils']['n_aoa']
    aoa   = wt_init_options['airfoils']['aoa']
    n_Re  = wt_init_options['airfoils']['n_Re']
    n_tab = wt_init_options['airfoils']['n_tab']
    n_xy  = wt_init_options['airfoils']['n_xy']
    
    name    = n_af * ['']
    ac      = np.zeros(n_af)
    r_thick = np.zeros(n_af)
    Re_all  = []
    for i in range(n_af):
        name[i]     = airfoils[i]['name']
        ac[i]       = airfoils[i]['aerodynamic_center']
        r_thick[i]  = airfoils[i]['relative_thickness']
        for j in range(len(airfoils[i]['polars'])):
            Re_all.append(airfoils[i]['polars'][j]['re'])
    Re = sorted(np.unique(Re_all)) 
    
    cl = np.zeros((n_af, n_aoa, n_Re, n_tab))
    cd = np.zeros((n_af, n_aoa, n_Re, n_tab))
    cm = np.zeros((n_af, n_aoa, n_Re, n_tab))
    
    coord_xy = np.zeros((n_af, n_xy, 2))
    
    for i in range(n_af):
        # for j in range(n_Re):
        cl[i,:,0,0] = np.interp(aoa / 180. * np.pi, airfoils[i]['polars'][0]['c_l']['grid'], airfoils[i]['polars'][0]['c_l']['values'])
        cd[i,:,0,0] = np.interp(aoa / 180. * np.pi, airfoils[i]['polars'][0]['c_d']['grid'], airfoils[i]['polars'][0]['c_d']['values'])
        cm[i,:,0,0] = np.interp(aoa / 180. * np.pi, airfoils[i]['polars'][0]['c_m']['grid'], airfoils[i]['polars'][0]['c_m']['values'])
    
        if cl[i,0,0,0] != cl[i,-1,0,0]:
            cl[i,0,0,0] = cl[i,-1,0,0]
            print("Airfoil " + name[i] + ' has the lift coefficient different between + and - 180 deg. This is fixed automatically, but please check the input data.')
        if cd[i,0,0,0] != cd[i,-1,0,0]:
            cd[i,0,0,0] = cd[i,-1,0,0]
            print("Airfoil " + name[i] + ' has the drag coefficient different between + and - 180 deg. This is fixed automatically, but please check the input data.')
        if cm[i,0,0,0] != cm[i,-1,0,0]:
            cm[i,0,0,0] = cm[i,-1,0,0]
            print("Airfoil " + name[i] + ' has the moment coefficient different between + and - 180 deg. This is fixed automatically, but please check the input data.')
        
        points = np.column_stack((airfoils[i]['coordinates']['x'], airfoils[i]['coordinates']['y']))
        # Check that airfoil points are declared from the TE suction side to TE pressure side
        idx_le = np.argmin(points[:,0])
        if np.mean(points[:idx_le,1]) > 0.:
            points = np.flip(points, axis=0)
        
        # Remap points using class AirfoilShape
        af = AirfoilShape(points=points)
        af.redistribute(n_xy, even=False, dLE=True)
        s = af.s
        af_points = af.points
        
        # Add trailing edge point if not defined
        if [1,0] not in af_points.tolist():
            af_points[:,0] -= af_points[np.argmin(af_points[:,0]), 0]
        c = max(af_points[:,0])-min(af_points[:,0])
        af_points[:,:] /= c
        
        coord_xy[i,:,:] = af_points
        
        # Plotting
        # import matplotlib.pyplot as plt
        # plt.plot(af_points[:,0], af_points[:,1], '.')
        # plt.plot(af_points[:,0], af_points[:,1])
        # plt.show()
        
        
    wt_opt['airfoils.aoa']       = aoa
    wt_opt['airfoils.name']      = name
    wt_opt['airfoils.ac']        = ac
    wt_opt['airfoils.r_thick']   = r_thick
    wt_opt['airfoils.Re']        = Re  # Not yet implemented!
    wt_opt['airfoils.tab']       = 0.  # Not yet implemented!
    wt_opt['airfoils.cl']        = cl
    wt_opt['airfoils.cd']        = cd
    wt_opt['airfoils.cm']        = cm
    
    wt_opt['airfoils.coord_xy']  = coord_xy
     
    return wt_opt

def assign_blade_values(wt_opt, wt_init_options, blade):
    # Function to assign values to the openmdao group Blade
    wt_opt = assign_outer_shape_bem_values(wt_opt, wt_init_options, blade['outer_shape_bem'])
    wt_opt = assign_internal_structure_2d_fem_values(wt_opt, wt_init_options, blade['internal_structure_2d_fem'])
    
    return wt_opt
    
def assign_outer_shape_bem_values(wt_opt, wt_init_options, outer_shape_bem):
    # Function to assign values to the openmdao component Blade_Outer_Shape_BEM
    
    n_span      = wt_init_options['blade']['n_span']
    nd_span     = wt_init_options['blade']['nd_span']
    n_af_span   = wt_init_options['blade']['n_af_span']
    
    wt_opt['blade.outer_shape_bem.af_used']     = outer_shape_bem['airfoil_position']['labels']
    wt_opt['blade.outer_shape_bem.af_position'] = outer_shape_bem['airfoil_position']['grid']
    
    wt_opt['blade.outer_shape_bem.s']           = nd_span
    wt_opt['blade.outer_shape_bem.chord']       = np.interp(nd_span, outer_shape_bem['chord']['grid'], outer_shape_bem['chord']['values'])
    wt_opt['blade.outer_shape_bem.twist']       = np.interp(nd_span, outer_shape_bem['twist']['grid'], outer_shape_bem['twist']['values']) * 180. / np.pi
    wt_opt['blade.outer_shape_bem.pitch_axis']  = np.interp(nd_span, outer_shape_bem['pitch_axis']['grid'], outer_shape_bem['pitch_axis']['values'])
    
    wt_opt['blade.outer_shape_bem.ref_axis'][:,0]  = np.interp(nd_span, outer_shape_bem['reference_axis']['x']['grid'], outer_shape_bem['reference_axis']['x']['values'])
    wt_opt['blade.outer_shape_bem.ref_axis'][:,1]  = np.interp(nd_span, outer_shape_bem['reference_axis']['y']['grid'], outer_shape_bem['reference_axis']['y']['values'])
    wt_opt['blade.outer_shape_bem.ref_axis'][:,2]  = np.interp(nd_span, outer_shape_bem['reference_axis']['z']['grid'], outer_shape_bem['reference_axis']['z']['values'])
    
    return wt_opt
    
def assign_internal_structure_2d_fem_values(wt_opt, wt_init_options, internal_structure_2d_fem):
    # Function to assign values to the openmdao component Blade_Internal_Structure_2D_FEM
    n_span          = wt_init_options['blade']['n_span']
    n_webs          = wt_init_options['blade']['n_webs']
    
    web_name        = n_webs * ['']
    webs_rotation   = np.zeros((n_webs, n_span))
    webs_offset_y_pa= np.zeros((n_webs, n_span))
    definition_web  = np.zeros(n_webs)
    nd_span         = wt_opt['blade.outer_shape_bem.s']
    
    for i in range(n_webs):
        web_name[i] = internal_structure_2d_fem['webs'][i]['name']
        if 'rotation' in internal_structure_2d_fem['webs'][i] and 'offset_y_pa' in internal_structure_2d_fem['webs'][i]:
            if 'fixed' in internal_structure_2d_fem['webs'][i]['rotation'].keys():
                if internal_structure_2d_fem['webs'][i]['rotation']['fixed'] == 'twist':
                    definition_web[i] = 1
                else:
                    exit('Invalid rotation reference for web ' + web_name[i] + '. Please check the yaml input file')
            else:
                webs_rotation[i,:] = np.interp(nd_span, internal_structure_2d_fem['webs'][i]['rotation']['grid'], internal_structure_2d_fem['webs'][i]['rotation']['values']) * 180. / np.pi
                definition_web[i] = 2
            webs_offset_y_pa[i,:] = np.interp(nd_span, internal_structure_2d_fem['webs'][i]['offset_y_pa']['grid'], internal_structure_2d_fem['webs'][i]['offset_y_pa']['values'])
        else:
            exit('Webs definition not supported. Please check the yaml input.')
    
    n_layers        = wt_init_options['blade']['n_layers']
    layer_name      = n_layers * ['']
    layer_mat       = n_layers * ['']
    thickness       = np.zeros((n_layers, n_span))
    fiber_orient    = np.zeros((n_layers, n_span))
    layer_rotation  = np.zeros((n_layers, n_span))
    layer_offset_y_pa  = np.zeros((n_layers, n_span))
    layer_width     = np.zeros((n_layers, n_span))
    layer_midpoint_nd  = np.zeros((n_layers, n_span))
    layer_start_nd  = np.zeros((n_layers, n_span))
    layer_end_nd    = np.zeros((n_layers, n_span))
    layer_web       = n_layers * ['']
    layer_side      = n_layers * ['']
    definition_layer= np.zeros(n_layers)
    
    for i in range(n_layers):
        layer_name[i]  = internal_structure_2d_fem['layers'][i]['name']
        layer_mat[i]   = internal_structure_2d_fem['layers'][i]['material']
        thickness[i]   = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['thickness']['grid'], internal_structure_2d_fem['layers'][i]['thickness']['values'])
        if 'rotation' in internal_structure_2d_fem['layers'][i] and 'offset_y_pa' in internal_structure_2d_fem['layers'][i] and 'width' in internal_structure_2d_fem['layers'][i] and 'side' in internal_structure_2d_fem['layers'][i]:
            if 'fixed' in internal_structure_2d_fem['layers'][i]['rotation'].keys():
                if internal_structure_2d_fem['layers'][i]['rotation']['fixed'] == 'twist':
                    definition_layer[i] = 1
                else:
                    exit('Invalid rotation reference for layer ' + layer_name[i] + '. Please check the yaml input file.')
            else:
                layer_rotation[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['rotation']['grid'], internal_structure_2d_fem['layers'][i]['rotation']['values']) * 180. / np.pi
                definition_layer[i] = 2
            layer_offset_y_pa[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['offset_y_pa']['grid'], internal_structure_2d_fem['layers'][i]['offset_y_pa']['values'])
            layer_width[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['width']['grid'], internal_structure_2d_fem['layers'][i]['width']['values'])
            layer_side[i]    = internal_structure_2d_fem['layers'][i]['side']
        if 'midpoint_nd_arc' in internal_structure_2d_fem['layers'][i]:
            if 'fixed' in internal_structure_2d_fem['layers'][i]['midpoint_nd_arc'].keys():
                if internal_structure_2d_fem['layers'][i]['midpoint_nd_arc']['fixed'] == 'TE':
                    layer_midpoint_nd[i,:] = np.ones(n_span)
                elif internal_structure_2d_fem['layers'][i]['midpoint_nd_arc']['fixed'] == 'LE':
                    layer_midpoint_nd[i,:] = -np.ones(n_span) # To be assigned later!
            else:
                layer_midpoint_nd[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['midpoint_nd_arc']['grid'], internal_structure_2d_fem['layers'][i]['midpoint_nd_arc']['values'])
        if 'start_nd_arc' in internal_structure_2d_fem['layers'][i]:
            if 'fixed' in internal_structure_2d_fem['layers'][i]['start_nd_arc'].keys():
                if internal_structure_2d_fem['layers'][i]['start_nd_arc']['fixed'] == 'TE':
                    layer_start_nd[i,:] = np.ones(n_span)
                else:
                    layer_start_nd[i,:] = -np.ones(n_span) # To be assigned later!
            else:
                layer_start_nd[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['start_nd_arc']['grid'], internal_structure_2d_fem['layers'][i]['start_nd_arc']['values'])
        if 'end_nd_arc' in internal_structure_2d_fem['layers'][i]:
            if 'fixed' in internal_structure_2d_fem['layers'][i]['end_nd_arc'].keys():
                if internal_structure_2d_fem['layers'][i]['end_nd_arc']['fixed'] == 'TE':
                    layer_end_nd[i,:] = np.ones(n_span)
                else:
                    layer_end_nd[i,:] = -np.ones(n_span) # To be assigned later!
            else:
                layer_end_nd[i,:] = np.interp(nd_span, internal_structure_2d_fem['layers'][i]['end_nd_arc']['grid'], internal_structure_2d_fem['layers'][i]['end_nd_arc']['values'])
        if 'web' in internal_structure_2d_fem['layers'][i]:
            layer_web[i] = internal_structure_2d_fem['layers'][i]['web']

    wt_opt['blade.internal_structure_2d_fem.web_name']          = web_name
    wt_opt['blade.internal_structure_2d_fem.s']                 = nd_span
    wt_opt['blade.internal_structure_2d_fem.webs_rotation']     = webs_rotation
    wt_opt['blade.internal_structure_2d_fem.webs_offset_y_pa']  = webs_offset_y_pa
    
    wt_opt['blade.internal_structure_2d_fem.layer_name']        = layer_name
    wt_opt['blade.internal_structure_2d_fem.layer_mat']         = layer_mat
    wt_opt['blade.internal_structure_2d_fem.layer_side']        = layer_side
    wt_opt['blade.internal_structure_2d_fem.layer_rotation']    = layer_rotation
    wt_opt['blade.internal_structure_2d_fem.layer_offset_y_pa'] = layer_offset_y_pa
    wt_opt['blade.internal_structure_2d_fem.layer_width']       = layer_width
    wt_opt['blade.internal_structure_2d_fem.layer_midpoint_nd'] = layer_midpoint_nd
    wt_opt['blade.internal_structure_2d_fem.layer_start_nd']    = layer_start_nd
    wt_opt['blade.internal_structure_2d_fem.layer_end_nd']      = layer_end_nd
    wt_opt['blade.internal_structure_2d_fem.layer_web']         = layer_web
    wt_opt['blade.internal_structure_2d_fem.definition_web']    = definition_web
    wt_opt['blade.internal_structure_2d_fem.definition_layer']  = definition_layer
    
    return wt_opt
    
if __name__ == "__main__":

    ## File management
    fname_input        = "reference_turbines/nrel5mw/nrel5mw_mod_update.yaml"
    # fname_input        = "/mnt/c/Material/Projects/Hitachi_Design/Design/turbine_inputs/aerospan_formatted_v13.yaml"
    fname_output       = "reference_turbines/nrel5mw/nrel5mw_mod_update_output.yaml"
    
    wt_initial              = WT_Data()
    wt_initial.validate     = False
    wt_initial.fname_schema = "reference_turbines/IEAontology_schema.yaml"
    wt_init_options, blade, tower, nacelle, materials, airfoils = wt_initial.initialize(fname_input)
    
    wt_opt          = Problem()
    wt_opt.model    = Wind_Turbine(wt_init_options = wt_init_options)
    wt_opt.setup()
    wt_opt = yaml2openmdao(wt_opt, wt_init_options, blade, tower, nacelle, materials, airfoils)
    wt_opt.run_driver()
    
    print(wt_opt['blade.interp_airfoils.r_thick_interp'])
