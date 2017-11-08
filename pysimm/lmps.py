# ******************************************************************************
# pysimm.lmps module
# ******************************************************************************
#
# ******************************************************************************
# License
# ******************************************************************************
# The MIT License (MIT)
#
# Copyright (c) 2016 Michael E. Fortunato, Coray M. Colina
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import shlex
import shutil
from subprocess import call, Popen, PIPE
from Queue import Queue, Empty
from threading import Thread
import os
import sys
import json
from random import randint
from time import strftime

from pysimm.system import read_lammps
from pysimm.system import System
from pysimm import error_print
from pysimm import warning_print
from pysimm import verbose_print
from pysimm import debug_print
from pysimm.utils import PysimmError, Item, ItemContainer

try:
    from Rappture.tools import getCommandOutput as RapptureExec
except ImportError:
    pass

LAMMPS_EXEC = os.environ.get('LAMMPS_EXEC')
verbose = False
templates = {}

FF_SETTINGS = {
    'dreiding':
        {
            'pair_style':       'buck',
            'bond_style':       'harmonic',
            'angle_style':      'harmonic',
            'dihedral_style':   'harmonic',
            'improper_style':   'harmonic',
            'pair_mix':         'arithmetic',
            'special_bonds':    'dreiding'
        },
    'amber':
        {
            'pair_style':       'lj/cut',
            'bond_style':       'harmonic',
            'angle_style':      'harmonic',
            'dihedral_style':   'fourier',
            'improper_style':   'cvff',
            'pair_mix':         'arithmetic',
            'special_bonds':    'amber'
        },
    'pcff':
        {
            'pair_style':       'lj/class2',
            'bond_style':       'class2',
            'angle_style':      'class2',
            'dihedral_style':   'class2',
            'improper_style':   'class2',
            'pair_mix':         'sixthpower',
            'special_bonds':    'lj/coul 0 0 1'
        },
    'opls':
        {
            'pair_style':       'lj/cut',
            'bond_style':       'harmonic',
            'angle_style':      'harmonic',
            'dihedral_style':   'opls',
            'improper_style':   'cvff',
            'pair_mix':         'geometric',
            'special_bonds':    'lj/coul 0 0 0.5'
        },
    'charmm':
        {
            'pair_style':       'lj/charmm',
            'bond_style':       'harmonic',
            'angle_style':      'charmm',
            'dihedral_style':   'charmm',
            'improper_style':   'harmonic',
            'pair_mix':         'arithmetic',
            'special_bonds':    'charmm'
        }
}

def check_lmps_exec():
    if LAMMPS_EXEC is None:
        print 'you must set environment variable LAMMPS_EXEC'
        return False
    else:
        try:
            stdout, stderr = Popen([LAMMPS_EXEC, '-e', 'both', '-l', 'none'],
                                   stdin=PIPE, stdout=PIPE,
                                   stderr=PIPE).communicate()
            if verbose:
                print 'using %s LAMMPS machine' % LAMMPS_EXEC
            return True
        except OSError:
            print 'LAMMPS is not configured properly for one reason or another'
            return False


class Init(object):
    def __init__(self, **kwargs):
        self.forcefield = kwargs.get('forcefield')
        self.units = kwargs.get('units', 'real')
        self.atom_style = kwargs.get('atom_style', 'full')
        self.charge = kwargs.get('charge')
        self.kspace_style = kwargs.get('kspace_style', 'pppm 1e-4')
        self.cutoff = kwargs.get('cutoff')
        self.pair_style = kwargs.get('pair_style')
        self.bond_style = kwargs.get('bond_style')
        self.angle_style = kwargs.get('angle_style')
        self.dihedral_style = kwargs.get('dihedral_style')
        self.improper_style = kwargs.get('improper_style')
        self.special_bonds = kwargs.get('special_bonds')
        self.pair_modify = kwargs.get('pair_modify', {})
        self.read_data = kwargs.get('read_data')
        
        if self.forcefield and self.forcefield not in ['amber', 'dreiding', 'pcff', 'opls', 'charmm']:
            if self.forcefield.lower() in ['gaff', 'gaff2']:
                self.forcefield = 'amber'
            elif self.forcefield.lower() in ['cgenff']:
                self.forcefield = 'charmm'
            else:
                warning_print('{} forcefield not supported yet'.format(self.forcefield))

        if isinstance(self.cutoff, int) or isinstance(self.cutoff, float):
            self.cutoff = {'lj': self.cutoff, 'coul': self.cutoff, 'inner_lj': self.cutoff-2.0}
        if self.cutoff is None:
            self.cutoff = {'lj': 12.0, 'coul': 12.0, 'inner_lj': 10.0}

    def write(self, sim):
        s = sim.system
        
        if self.forcefield is None and s.forcefield is not None:
            self.forcefield = s.forcefield
            
        if self.special_bonds is None and self.forcefield is not None:
            self.special_bonds = FF_SETTINGS[self.forcefield]['special_bonds']
            
        if self.pair_modify.get('mix') is None and self.forcefield is not None:
            self.pair_modify['mix'] = FF_SETTINGS[self.forcefield]['pair_mix']

        if self.charge is None and s is not None:
            for p in s.particles:
                if p.charge:
                    self.charge = True
                    break
            if self.charge is None:
                self.charge=False

        lammps_input = ''
        lammps_input += '\n' + '#'*80 + '\n'
        lammps_input += '#'*34 + '    Init    ' + '#'*34 + '\n'
        lammps_input += '#'*80 + '\n'
        lammps_input += '{:<15} {}\n'.format('units', self.units)
        lammps_input += '{:<15} {}\n'.format('atom_style', self.atom_style)

        if self.pair_style:
            lammps_input += '{:<15} {}'.format('pair_style', self.pair_style)
        elif self.forcefield:
            self.pair_style = FF_SETTINGS[self.forcefield]['pair_style']
            lammps_input += '{:<15} {}'.format('pair_style', self.pair_style)
            if self.charge:
                lammps_input += '/coul/long'
                self.pair_style += '/coul/long'
        if self.cutoff:
            if self.forcefield == ['charmm'] and self.cutoff.get('inner_lj'):
                lammps_input += ' {} '.format(self.cutoff['inner_lj'])
            lammps_input += ' {} '.format(self.cutoff['lj'])
            if self.charge and self.cutoff.get('coul'):
                lammps_input += ' {} '.format(self.cutoff['coul'])
        lammps_input += '\n'
        
        if self.charge:
            lammps_input += '{:<15} {}\n'.format('kspace_style', self.kspace_style)
        
        if self.bond_style is None and s and s.bonds.count > 0:
            if self.forcefield:
                self.bond_style = FF_SETTINGS[self.forcefield]['bond_style']
        if self.bond_style:
            lammps_input += '{:<15} {}\n'.format('bond_style', self.bond_style)
            
        if self.angle_style is None and s and s.angles.count > 0:
            if self.forcefield:
                self.angle_style = FF_SETTINGS[self.forcefield]['angle_style']
        if self.angle_style:
            lammps_input += '{:<15} {}\n'.format('angle_style', self.angle_style)
            
        if self.dihedral_style is None and s and s.dihedrals.count > 0:
            if self.forcefield:
                self.dihedral_style = FF_SETTINGS[self.forcefield]['dihedral_style']
        if self.dihedral_style:
            lammps_input += '{:<15} {}\n'.format('dihedral_style', self.dihedral_style)
            
        if self.improper_style is None and s and s.impropers.count > 0:
            if self.forcefield:
                self.improper_style = FF_SETTINGS[self.forcefield]['improper_style']
        if self.improper_style:
            lammps_input += '{:<15} {}\n'.format('improper_style', self.improper_style)
            
        if self.special_bonds:
            lammps_input += '{:<15} {}\n'.format('special_bonds', self.special_bonds)
        
        if self.pair_modify:
            lammps_input += '{:<15} '.format('pair_modify')
            for k, v in self.pair_modify.items():
                lammps_input += '{} {} '.format(k, v)
            lammps_input += '\n'
            
        if self.read_data:
            lammps_input += '{:<15} {}\n'.format('read_data', self.read_data)
        elif s:
            s.write_lammps('temp.lmps')
            lammps_input += '{:<15} temp.lmps\n'.format('read_data')
            
        if self.pair_style and self.pair_style.startswith('buck'):
            for pt1 in s.particle_types:
                for pt2 in s.particle_types:
                    if pt1.tag <= pt2.tag:
                        a = pow(pt1.a*pt2.a, 0.5)
                        c = pow(pt1.c*pt2.c, 0.5)
                        rho = 0.5*(pt1.rho+pt2.rho)
                        lammps_input += '{:<15} {} {} {} {} {}\n'.format('pair_coeff', pt1.tag, pt2.tag, a, rho, c)
        
        lammps_input += '#'*80 + '\n\n'
        
        return lammps_input
        

class Group(Item):
    def __init__(self, name='all', style='id', *args, **kwargs):
        Item.__init__(self, name=name, style=style, args=args, **kwargs)
        
    def write(self, sim):
        inp = '{:<15} {name} {style} '.format('group', name=self.name, style=self.style)
        for a in self.args:
            inp += '{} '.format(a)
        if not self.args:
            inp += '*'
        inp += '\n'
        return inp
        

class Velocity(Item):
    def __init__(self, group=Group('all'), style='create', *args, **kwargs):
        Item.__init__(self, group=group, style=style, args=args, **kwargs)
        if self.seed is None:
            self.seed = randint(10000, 99999)
        if self.temperature is None:
            self.temperature = 300.0
        if args:
            self.from_args = True
        
    def write(self, sim):
        if isinstance(self.group, Group):
            inp = '{:<15} {group.name} {style} '.format('velocity', group=self.group, style=self.style)
        else:
            inp = '{:<15} {group} {style} '.format('velocity', group=self.group, style=self.style)
        if self.from_args:
            for a in self.args:
                inp += '{} '.format(a)
        elif self.style == 'create' or self.style == 'scale':
            inp += '{temp} '.format(temp=self.temperature)
            if self.style == 'create':
                inp += '{seed} '.format(seed=self.seed)
        for k in ['dist', 'sum', 'mom', 'rot', 'bias', 'loop', 'rigid', 'units']:
            if getattr(self, k):
                inp += '{} {} '.format(k, getattr(self, k))
        inp += '\n'
        return inp


class OutputSettings(object):
    def __init__(self, **kwargs):
        self.thermo = kwargs.get('thermo')
        self.dump = kwargs.get('dump', kwargs.get('trajectory'))
        
        if isinstance(self.thermo, int):
            self.thermo = {'freq': self.thermo}
        if isinstance(self.thermo, dict):
            self.thermo['freq'] = self.thermo.get('freq', 1000)
            self.thermo['style'] = self.thermo.get('style', 'custom')
            self.thermo['args'] = self.thermo.get('args', ['step', 'time', 'temp', 'vol', 'press', 'etotal', 'epair', 'emol', 'density'])
            self.thermo['modify'] = self.thermo.get('modify')
            
        if isinstance(self.dump, int):
            self.dump = {'freq': self.dump}
        if isinstance(self.dump, dict):
            self.dump['freq'] = self.dump.get('freq', 1000)
            self.dump['group'] = self.dump.get('group', Group(name='all'))
            self.dump['name'] = self.dump.get('name', 'pysimm_dump')
            self.dump['style'] = self.dump.get('style', 'custom')
            self.dump['filename'] = self.dump.get('filename', 'dump.*')
            self.dump['args'] = self.dump.get('args', ['id', 'type', 'mol', 'x', 'y', 'z', 'vx', 'vy', 'vz'])
            self.dump['modify'] = self.dump.get('modify')
        
        if isinstance(self.dump, dict) and isinstance(self.dump['group'], basestring):
            self.dump['group'] = Group(name=self.dump['group'])
            
    def write(self, sim):
        lammps_input = ''
            
        if isinstance(self.thermo, dict):
            lammps_input += '\n' + '#'*80 + '\n'
            lammps_input += '#'*29 + '    Thermo  output    ' + '#'*29 + '\n'
            lammps_input += '#'*80 + '\n'
            lammps_input += '{:<15} {}\n'.format('thermo', self.thermo['freq'])
            lammps_input += '{:<15} {} '.format('thermo_style', self.thermo['style'])
            if self.thermo['style'] == 'custom':
                lammps_input += ' '.join(self.thermo['args'])
            lammps_input += '\n'
            if self.thermo.get('modify'):
                lammps_input += '{:<15} {} '.format('thermo_modify', self.thermo.get('modify'))
                lammps_input += '\n'
            lammps_input += '#'*80 + '\n\n'
        
        if isinstance(self.dump, dict):
            lammps_input += '\n' + '#'*80 + '\n'
            lammps_input += '#'*30 + '    Dump  output    ' + '#'*30 + '\n'
            lammps_input += '#'*80 + '\n'
            lammps_input += '{:<15} {} {} {} {} {} '.format('dump', self.dump['name'], self.dump['group'].name, self.dump['style'], self.dump['freq'], self.dump['filename'])
            if self.dump['style'] == 'custom':
                lammps_input += ' '.join(self.dump['args'])
            lammps_input += '\n'
            if self.dump.get('modify'):
                lammps_input += '{:<15} {} '.format('dump_modify', self.dump.get('modify'))
                lammps_input += '\n'
            lammps_input += '#'*80 + '\n\n'
        return lammps_input
            

class Qeq(object):
    """pysimm.lmps.MolecularDynamics

    Template object to contain LAMMPS qeq settings

    Attributes:
        cutoff: distance cutoff for charge equilibration
        tol: tolerance (precision) for charge equilibration
        max_iter: maximum iterations
        qfile: file with qeq parameters (leave undefined for defaults)
    """
    def __init__(self, **kwargs):
        self.cutoff = kwargs.get('cutoff', 10)
        self.tol = kwargs.get('tol', 1.0e-6)
        self.max_iter = kwargs.get('max_iter', 200)
        self.qfile = kwargs.get('qfile')
        
        self.input = ''
        
    def write(self, sim):
        """pysimm.lmps.Qeq.write

        Create LAMMPS input for a charge equilibration calculation

        Args:
            sim: :class:`~pysimm.lmps.Simulation` object reference

        Returns:
            input string
        """
        if self.qfile is None:
            param_file = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                      os.pardir, 'dat', 'qeq', 'hcno.json')
            with file(param_file) as f:
                qeq_params = json.loads(f.read())
            with file('pysimm.qeq.tmp', 'w') as f:
                for pt in sim.system.particle_types:
                    f.write('{}\t{}\t{}\t{}\t{}\t{}\n'.format(pt.tag, 
                                                  qeq_params[pt.elem]['chi'],
                                                  qeq_params[pt.elem]['eta']*2,
                                                  qeq_params[pt.elem]['gamma'],
                                                  qeq_params[pt.elem]['zeta'],
                                                  qeq_params[pt.elem]['qcore']))
            self.qfile = 'pysimm.qeq.tmp'
        
        self.input = ''
        self.input += 'fix 1 all qeq/point 1 {} {} {} {}\n'.format(self.cutoff, self.tol, self.max_iter, self.qfile)
        self.input += 'run 0\n'
        self.input += 'unfix 1\n'
        
        return self.input
        
    
class MolecularDynamics(object):
    """pysimm.lmps.MolecularDynamics

    Template object to contain LAMMPS MD settings

    Attributes:
        timestep: timestep value to use during MD
        ensemble: 'nvt' or 'npt' or 'nve'
        limit: numerical value to use with nve when limiting particle displacement
        temp: temperature for use with 'nvt' and 'npt' or new_v
        tdamp: damping parameter for thermostat (default=100*timestep)
        pressure: pressure for use with 'npt'
        pdamp: damping parameter for barostat (default=1000*timestep)
        new_v: True to have LAMMPS generate new velocities
        seed: seed value for RNG (random by default)
        scale_v: True to scale velocities to given temperature default=False
        length: length of MD simulation in number of timesteps
        thermo: frequency to print thermodynamic data default=1000
        thermo_style: LAMMPS formatted input for thermo_style
        dump: frequency to dump trajectory
        dump_name: prefix of trajectory dump file
        dump_append: True to append to previous dump file is it exists
    """
    def __init__(self, **kwargs):

        self.name = kwargs.get('name', 'pysimm_md')
        self.group = kwargs.get('group', Group(name='all'))
        self.timestep = kwargs.get('timestep', 1)
        self.ensemble = kwargs.get('ensemble', 'nve')
        self.limit = kwargs.get('limit')
        self.temperature = kwargs.get('temperature', kwargs.get('temp', 300.))
        self.pressure = kwargs.get('pressure', 1.)
        self.new_v = kwargs.get('new_v')
        self.seed = kwargs.get('seed', randint(10000, 99999))
        self.scale_v = kwargs.get('scale_v')
        self.run = kwargs.get('run', kwargs.get('length', 2000))
        self.unfix = kwargs.get('unfix', True)
        self.rigid = kwargs.get('rigid')
        
        if kwargs.get('temp') is not None:
            print('temp keyword argument is deprecated for MolecularDynamics, please use temperature instead')
        
        if isinstance(self.group, basestring):
            self.group = Group(name=self.group)
        
        if isinstance(self.temperature, int) or isinstance(self.temperature, float):
            self.temperature = {'start': self.temperature}
            
        if isinstance(self.pressure, int) or isinstance(self.pressure, float):
            self.pressure = {'start': self.pressure}
            
        if isinstance(self.rigid, dict):
            self.ensemble = 'rigid/{}'.format(self.ensemble)
            if self.rigid.get('small'):
                self.ensemble += '/small '

        self.input = ''

    def write(self, sim):
        """pysimm.lmps.MolecularDynamics.write

        Create LAMMPS input for a molecular dynamics simulation.

        Args:
            sim: pysimm.lmps.Simulation object reference

        Returns:
            input string
        """
        self.input = ''

        self.input += '{:<15} {}\n'.format('timestep', self.timestep)
        
        self.input += '{:<15} {} {} {}'.format('fix', self.name, self.group.name, self.ensemble)
        if self.ensemble == 'nve' and self.limit:
            self.input += '/limit {} '.format(self.limit)
        else:
            self.input += ' '
        if self.rigid:
            self.input += '{} '.format(self.rigid.get('style', 'molecule'))
            if self.rigid.get('style') == 'group':
                assert isinstance(self.rigid.get('groups'), list)
                self.input += ' {} '.format(len(self.rigid.get('groups')))
                for g in self.rigid.get('groups'):
                    if isinstance(g, Group):
                        group_name = g.name
                    else:
                        group_name = g
                    self.input += '{} '.format(group_name)
        if 't' in self.ensemble:
            self.input += 'temp {} {} {} '.format(self.temperature.get('start', 300.), self.temperature.get('stop', self.temperature.get('start', 300.)), self.temperature.get('damp', 100*self.timestep))
        if 'p' in self.ensemble:
            self.input += '{} {} {} {} '.format(self.pressure.get('iso', 'aniso'), self.pressure.get('start', 1.), self.pressure.get('stop', self.pressure.get('start', 1.)), self.pressure.get('damp', 1000*self.timestep))
        self.input += '\n'

        if self.run:
            self.input += '{:<15} {}\n'.format('run', int(self.run))
        if self.unfix:
            self.input += 'unfix {}\n'.format(self.name)

        return self.input
        
        
class SteeredMolecularDynamics(MolecularDynamics):
    def __init__(self, **kwargs):
        MolecularDynamics.__init__(self, **kwargs)
        self.p1 = kwargs.get('p1')
        self.p2 = kwargs.get('p2')
        self.k = kwargs.get('k', 20.0)
        self.v = kwargs.get('v', 0.001)
        self.d = kwargs.get('d', 3.0)
    
    def write(self, sim):
        """pysimm.lmps.SteeredMolecularDynamics.write

        Create LAMMPS input for a steered molecular dynamics simulation.

        Args:
            sim: :class:`~pysimm.lmps.Simulation` object reference

        Returns:
            input string
        """
        self.input = ''
        if self.thermo:
            self.input += 'thermo %s\n' % int(self.thermo)
        if self.thermo_style:
            self.input += 'thermo_style %s\n' % self.thermo_style

        self.input += 'timestep %s\n' % self.timestep

        if self.ensemble == 'nvt':
            self.input += 'fix 1 all %s temp %s %s %s\n' % (self.ensemble, self.t_start, self.t_stop, self.tdamp)
        elif self.ensemble == 'npt':
            self.input += ('fix 1 all %s temp %s %s %s iso %s %s %s\n'
                           % (self.ensemble, self.t_start, self.t_stop, self.tdamp, self.p_start, self.p_stop, self.pdamp))
        elif self.ensemble == 'nve':
            self.input += 'fix 1 all %s\n' % self.ensemble

        if self.new_v:
            self.input += 'velocity all create %s %s\n' % (self.t_start, self.seed)
        elif self.scale_v:
            self.input += 'velocity all scale %s\n' % self.t_start

        if self.dump:
            if self.dump_name:
                self.input += ('dump pysimm_dump all atom %s %s.lammpstrj\n'
                               % (self.dump, self.dump_name))
            elif sim.name:
                self.input += ('dump pysimm_dump all atom %s %s.lammpstrj\n'
                               % (self.dump, '_'.join(sim.name.split())))
            else:
                self.input += ('dump pysimm_dump all atom %s pysimm_dump.lammpstrj\n'
                               % self.dump)
            if self.dump_append:
                self.input += 'dump_modify pysimm_dump append yes\n'
                
        self.input += 'group p1 id {}\n'.format(self.p1.tag)
        self.input += 'group p2 id {}\n'.format(self.p2.tag)
        self.input += 'fix steer p1 smd cvel {} {} couple p2 auto auto auto {}\n'.format(self.k, self.v, self.d)

        self.input += 'run %s\n' % int(self.length)
        self.input += 'unfix 1\n'
        self.input += 'unfix steer\n'
        if self.dump:
            self.input += 'undump pysimm_dump\n'

        return self.input
        


class Minimization(object):
    """pysimm.lmps.Minimization

    Template object to contain LAMMPS energy minimization settings.

    Attributes:
        min_style: LAMMPS minimization style default='sd'
        etol: energy tolerance default=1e-3
        ftol: force tolerance default=1e-3
        maxiter: maximum iterations default=10000
        max eval: maximum force evaluations default=100000
        thermo: frequency to print thermodynamic data default=1000
        thermo_style: LAMMPS formatted input for thermo_style
        dump: frequency to dump trajectory
        dump_name: prefix of trajectory dump file
        dump_append: True to append to previous dump file is it exists
    """
    def __init__(self, **kwargs):

        self.min_style = kwargs.get('min_style', 'fire')
        self.dmax = kwargs.get('dmax')
        self.etol = kwargs.get('etol', 1.0e-3)
        self.ftol = kwargs.get('ftol', 1.0e-3)
        self.maxiter = kwargs.get('maxiter', 10000)
        self.maxeval = kwargs.get('maxeval', 100000)

        self.input = ''

    def write(self, sim):
        """pysimm.lmps.Minimization.write

        Create LAMMPS input for an energy minimization simulation.

        Args:
            sim: :class:`~pysimm.lmps.Simulation` object reference

        Returns:
            input string
        """
        self.input = ''

        self.input += 'min_style %s\n' % self.min_style
        if self.dmax:
            self.input += 'min_modify dmax %s\n' % self.dmax
        self.input += ('minimize %s %s %s %s\n' % (self.etol, self.ftol,
                                                   self.maxiter, self.maxeval))

        return self.input


class CustomInput(object):
    """pysimm.lmps.CustomInput

    Template object to contain custom LAMMPS input.

    Attributes:
        custom_input: custom input string
    """
    def __init__(self, custom_input):
        self.input = '{}\n'.format(custom_input)

    def write(self, sim):
        """pysimm.lmps.CustomInput.write

        Create LAMMPS input for a custom simulation.

        Args:
            sim: pysimm.lmps.Simulation object reference

        Returns:
            input string
        """
        return self.input


class Simulation(object):
    """pysimm.lmps.Simulation

    Organizational object for LAMMPS simulation. Should contain combination of
    :class:`~pysimm.lmps.MolecularDynamics`, :class:`~pysimm.lmps.Minimization`, and/or :class:`~pysimm.lmps.CustomInput` object.

    Attributes:
        atom_style: LAMMPS atom_style default=full
        kspace_style: LAMMPS kspace style default='pppm 1e-4'
        units: LAMMPS set of units to use default=real
        special_bonds: LAMMPS special bonds input
        nonbond_mixing: type of mixing rule for nonbonded interactions default=arithmetic
        cutoff: cutoff for nonbonded interactions default=12
        name: name id for simulations
        log: prefix for LAMMPS log file
        write: file name to write final LAMMPS data file default=None
        print_to_screen: True to have LAMMPS output printed to stdout after simulation ends
        debug: True to have LAMMPS output streamed to stdout during simulation (WARNING: this may degrade performance)
    """
    def __init__(self, s, **kwargs):

        self.system = s
        
        self.forcefield = kwargs.get('forcefield')
        if self.forcefield is None and s and s.forcefield is not None:
            self.forcefield = s.forcefield

        self.debug = kwargs.get('debug', False)
        self.print_to_screen = kwargs.get('print_to_screen', False)
        self.name = kwargs.get('name', False)
        self.log = kwargs.get('log')
        self.write = kwargs.get('write', False)

        self._input = ''

        self.sim = kwargs.get('sim', [])
        
    def add(self, *args):
        for item in args:
            if isinstance(item, basestring):
                self.sim.append(CustomInput(item))
            else:
                self.sim.append(item)
        return item
        
    def add_qeq(self, template=None, **kwargs):
        """pysimm.lmps.Simulation.add_qeq

        Add :class:`~pysimm.lmps.Qeq` template to simulation

        Args:
            template: :class:`~pysimm.lmps.Qeq` object reference
            **kwargs: if template is None these are passed to :class:`~pysimm.lmps.Qeq` constructor to create new template
        """
        if template is None:
            self.sim.append(Qeq(**kwargs))
        elif isinstance(template, Qeq):
            self.sim.append(template)
        else:
            error_print('you must add an object of type Qeq to Simulation')

    def add_md(self, template=None, **kwargs):
        """pysimm.lmps.Simulation.add_md

        Add :class:`~pysimm.lmps.MolecularDyanmics` template to simulation

        Args:
            template: :class:`~pysimm.lmps.MolecularDynamics` object reference
            **kwargs: if template is None these are passed to :class:`~pysimm.lmps.MolecularDynamics` constructor to create new template
        """
        if template is None:
            self.sim.append(MolecularDynamics(**kwargs))
        elif isinstance(template, MolecularDynamics):
            self.sim.append(template)
        else:
            error_print('you must add an object of type MolecularDynamics to Simulation')

    def add_min(self, template=None, **kwargs):
        """pysimm.lmps.Simulation.add_min

        Add :class:`~pysimm.lmps.Minimization` template to simulation

        Args:
            template: :class:`~pysimm.lmps.Minimization` object reference
            **kwargs: if template is None these are passed to
            :class:`~pysimm.lmps.Minimization` constructor to create new template
        """
        if template is None:
            self.sim.append(Minimization(**kwargs))
        elif isinstance(template, Minimization):
            self.sim.append(template)
        else:
            error_print('you must add an object of type Minimization to Simulation')

    def add_custom(self, custom=''):
        """pysimm.lmps.Simulation.add_custom

        Add custom input string to simulation

        Args:
            custom: custom LAMMPS input string to add to Simulation
        """
        self.sim.append(CustomInput(custom))
       
    @property 
    def input(self):
        self.write_input()
        return self._input

    def write_input(self, init=True):
        """pysimm.lmps.Simulation.write_input

        Creates LAMMPS input string including initialization and input from templates/custom input

        Args:
            None

        Returns:
            None
        """
        self._input = ''
        
        for task in self.sim:
            if isinstance(task, Init):
                init = False

        if init:
            self.sim.insert(0, Init(forcefield=self.forcefield))

        for template in self.sim:
            self._input += template.write(self)
            
        self._input += 'write_dump all custom pysimm.dump.tmp id q x y z vx vy vz\n'

        self._input += 'quit\n'

    def run(self, np=None, nanohub=None, init=True, save_input=True, prefix='mpiexec'):
        """pysimm.lmps.Simulation.run

        Begin LAMMPS simulation.

        Args:
            np: number of threads to use (serial by default) default=None
            nanohub: dictionary containing nanohub resource information default=None
            rewrite: True to rewrite input before running default=True
            init: True to write initialization part of LAMMPS input script (set to False if using complete custom input)
        """
        self.write_input(init=init)
        if isinstance(save_input, str):
            with file(save_input, 'w') as f:
                f.write(self.input)
        elif save_input is True:
            with file('pysimm.sim.in', 'w') as f:
                f.write(self.input)
        try:
            call_lammps(self, np, nanohub, prefix=prefix)
        except OSError as ose:
            raise PysimmError('There was a problem calling LAMMPS with {}'.format(prefix)), None, sys.exc_info()[2]
        except IOError as ioe:
            if check_lmps_exec():
                raise PysimmError('There was a problem running LAMMPS. The process started but did not finish successfully. Check the log file, or rerun the simulation with debug=True to debug issue from LAMMPS output'), None, sys.exc_info()[2]
            else:
                raise PysimmError('There was a problem running LAMMPS. LAMMPS is not configured properly. Make sure the LAMMPS_EXEC environment variable is set to the correct LAMMPS executable path. The current path is set to:\n\n{}'.format(LAMMPS_EXEC)), None, sys.exc_info()[2]


def enqueue_output(out, queue):
    """pysimm.lmps.enqueue_output

    Helps queue output for printing to screen during simulation.
    """
    for line in iter(out.readline, b''):
        queue.put(line)
    out.close()


def call_lammps(simulation, np, nanohub, prefix='mpiexec'):
    """pysimm.lmps.call_lammps

    Wrapper to call LAMMPS using executable name defined in pysimm.lmps module.

    Args:
        simulation: :class:`~pysimm.lmps.Simulation` object reference
        np: number of threads to use
        nanohub: dictionary containing nanohub resource information default=None

    Returns:
        None
    """
    
    log_name = simulation.log or 'log.lammps'
    
    if nanohub:
        with file('temp.in', 'w') as f:
            f.write(simulation.input)
        if simulation.name:
            print('%s: sending %s simulation to computer cluster at nanoHUB' % (strftime('%H:%M:%S'), simulation.name))
        else:
            print('%s: sending simulation to computer cluster at nanoHUB' % strftime('%H:%M:%S'))
        sys.stdout.flush()
        cmd = ('submit -n %s -w %s -i temp.lmps -i temp.in '
               'lammps-09Dec14-parallel -e both -l none -i temp.in'
               % (nanohub.get('cores'), nanohub.get('walltime')))
        cmd = shlex.split(cmd)
        exit_status, stdo, stde = RapptureExec(cmd)
    else:
        if simulation.name:
            print('%s: starting %s LAMMPS simulation'
                  % (strftime('%H:%M:%S'), simulation.name))
        else:
            print('%s: starting LAMMPS simulation'
                  % strftime('%H:%M:%S'))
        if np:
            p = Popen([prefix, '-np', str(np),
                       LAMMPS_EXEC, '-e', 'both', '-l', log_name],
                      stdin=PIPE, stdout=PIPE, stderr=PIPE)
        else:
            p = Popen([prefix, LAMMPS_EXEC, '-e', 'both', '-l', log_name],
                      stdin=PIPE, stdout=PIPE, stderr=PIPE)
        simulation.write_input()
        if simulation.debug:
            print(simulation.input)
            warning_print('debug setting involves streaming output from LAMMPS process and can degrade performance')
            warning_print('only use debug for debugging purposes, use print_to_screen to collect stdout after process finishes')
            p.stdin.write(simulation.input)
            q = Queue()
            t = Thread(target=enqueue_output, args=(p.stdout, q))
            t.daemon = True
            t.start()
    
            while t.isAlive() or not q.empty():
                try:
                    line = q.get_nowait()
                except Empty:
                    pass
                else:
                    if simulation.debug:
                        sys.stdout.write(line)
                        sys.stdout.flush()
        else:
            stdo, stde = p.communicate(simulation.input)
            if simulation.print_to_screen:
                print(stdo)
                print(stde)
                    
    simulation.system.read_lammps_dump('pysimm.dump.tmp')

    try:
        os.remove('temp.lmps')
    except OSError as e:
        print e
        
    if os.path.isfile('pysimm.qeq.tmp'):
        os.remove('pysimm.qeq.tmp')
        
    try:
        os.remove('pysimm.dump.tmp')
        if simulation.name:
            print('%s: %s simulation using LAMMPS successful'
                  % (strftime('%H:%M:%S'), simulation.name))
        else:
            print('%s: simulation using LAMMPS successful'
                  % (strftime('%H:%M:%S')))
    except OSError as e:
        if simulation.name:
            raise PysimmError('%s simulation using LAMMPS UNsuccessful' % simulation.name)
        else:
            raise PysimmError('simulation using LAMMPS UNsuccessful')


def qeq(s, np=None, nanohub=None, **kwargs):
    """pysimm.lmps.qeq

    Convenience function to call a qeq calculation. kwargs are passed to :class:`~pysimm.lmps.Qeq` constructor

    Args:
        s: system to perform simulation on
        np: number of threads to use
        nanohub: dictionary containing nanohub resource information default=None

    Returns:
        None
    """
    sim = Simulation(s, **kwargs)
    sim.add_qeq(**kwargs)
    sim.run(np, nanohub)


def quick_md(s, np=None, nanohub=None, **kwargs):
    """pysimm.lmps.quick_md

    Convenience function to call an individual MD simulation. kwargs are passed to MD constructor

    Args:
        s: system to perform simulation on
        np: number of threads to use
        nanohub: dictionary containing nanohub resource information default=None

    Returns:
        None
    """
    sim = Simulation(s, **kwargs)
    sim.add_md(**kwargs)
    sim.run(np, nanohub)


def quick_min(s, np=None, nanohub=None, **kwargs):
    """pysimm.lmps.quick_min

    Convenience function to call an individual energy minimization simulation. kwargs are passed to min constructor

    Args:
        s: system to perform simulation on
        np: number of threads to use
        nanohub: dictionary containing nanohub resource information default=None

    Returns:
        None
    """
    sim = Simulation(s, **kwargs)
    sim.add_min(**kwargs)
    sim.run(np, nanohub)
    
    
def energy(s, all=False, np=None, **kwargs):
    """pysimm.lmps.energy

    Convenience function to calculate energy of a given :class:`~pysimm.system.System` object.

    Args:
        s: system to calculate energy
        all: returns decomposition of energy if True (default: False)
        np: number of threads to use for simulation

    Returns:
        total energy or disctionary of energy components
    """
    sim = Simulation(s, log='pysimm_calc.tmp.log', **kwargs)
    sim.add_md(length=0, thermo=1, thermo_style='custom step etotal epair emol evdwl ecoul ebond eangle edihed eimp', **kwargs)
    sim.run(np)
    with file('pysimm_calc.tmp.log') as f:
        line = f.next()
        while line.split()[0] != 'Step':
            line = f.next()
        line = f.next()
        step, etotal, epair, emol, evdwl, ecoul, ebond, eangle, edihed, eimp = map(float, line.split())
    try:
        os.remove('pysimm_calc.tmp.log')
    except:
        error_print('error likely occurred during simulation')
    if all:
        return {
                'step': int(step),
                'etotal': etotal,
                'epair': epair,
                'emol': emol,
                'evdwl': evdwl,
                'ecoul': ecoul,
                'ebond': ebond,
                'eangle': eangle,
                'edihed': edihed,
                'eimp': eimp
               }
    else:
        return etotal
        

class LogFile(object):
    def __init__(self, fname):
        self.filename = fname
        self.data = pd.DataFrame()
        self._read(self.filename)

    def _read(self, fname):
        with open(fname) as fr:
            copy = False
            for line in fr:
                if line.startswith('Step'):
                    strio = StringIO()
                    copy = True
                    names = line.strip().split()
                elif line.startswith('Loop'):
                    copy = False
                    strio.seek(0)
                    self.data = self.data.append(pd.read_table(strio, sep='\s+', names=names, index_col='Step'))
                elif copy:
                    strio.write(line)

