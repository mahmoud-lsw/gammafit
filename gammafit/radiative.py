# -*- coding: utf-8 -*-
# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import numpy as np
from .extern.validator import validate_scalar, validate_array, validate_physical_type

from .utils import trapz_loglog

__all__ = ['Synchrotron', 'InverseCompton', 'PionDecay', 'Bremsstrahlung', 'PionDecayKelner06']

from astropy.extern import six
import os
from astropy.utils.data import get_pkg_data_filename
import warnings
import logging
# Get a new logger to avoid changing the level of the astropy logger
log = logging.getLogger('gammafit.radiative')
log.setLevel(logging.INFO)

# Constants and units
from astropy import units as u
# import constant values from astropy.constants
from astropy.constants import c, G, m_e, h, hbar, k_B, R_sun, sigma_sb, e, m_p, M_sun, alpha
e = e.gauss

mec2 = (m_e * c ** 2).cgs
mec2_unit = u.Unit(mec2)

ar = (4 * sigma_sb / c).to('erg/(cm3 K4)')
r0 = (e**2 / mec2).to('cm')

def _validate_ene(ene):
    from astropy.table import Table

    if isinstance(ene, dict) or isinstance(ene, Table):
        try:
            ene = validate_array('energy',u.Quantity(ene['energy']),physical_type='energy')
        except KeyError:
            raise TypeError('Table or dict does not have \'energy\' column')
    else:
        if not isinstance(ene,u.Quantity):
            ene = u.Quantity(ene)
        validate_physical_type('energy',ene,physical_type='energy')

    return ene

class BaseRadiative(object):
    """Base class for radiative models

    This class implements the flux, sed methods and subclasses must implement the
    spectrum method which returns the intrinsic differential spectrum.
    """

    def flux(self, photon_energy, distance=1*u.kpc):
        """Differential flux at a given distance from the source.

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` float or array
            Photon energy array.

        distance : :class:`~astropy.units.Quantity` float, optional
            Distance to the source. Default is 1 kpc.
        """

        spec = self.spectrum(photon_energy)

        distance = validate_scalar('distance', distance, physical_type='length')
        spec /= 4 * np.pi * distance.to('cm') ** 2

        return spec.to('1/(s cm2 eV)')

    def sed(self, photon_energy, distance=1*u.kpc):
        """Spectral energy distribution at a given distance from the source.

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` float or array
            Photon energy array.

        distance : :class:`~astropy.units.Quantity` float, optional
            Distance to the source. Default is 1 kpc.
        """

        sed = (self.flux(photon_energy,distance) * photon_energy ** 2.).to('erg/(cm2 s)')

        return sed


class BaseElectron(BaseRadiative):
    """Implements gam and nelec properties in addition to the BaseRadiative methods
    """

    @property
    def _gam(self):
        """ Lorentz factor array
        """
        return np.logspace(self.log10gmin,self.log10gmax,
                self.ngamd*(self.log10gmax - self.log10gmin))

    @property
    def _nelec(self):
        """ Particles per unit lorentz factor
        """
        pd = self.particle_distribution(self._gam * mec2)
        return pd.to(1/mec2_unit).value

    @property
    def We(self):
        """ Total energy in electrons
        """
        return trapz_loglog(self._gam * self._nelec, self._gam * mec2)


class Synchrotron(BaseElectron):
    """Synchrotron emission from an electron population.

    Parameters
    ----------
    particle_distribution : function
        Particle distribution function, taking electron energies as a
        `~astropy.units.Quantity` array or float, and returning the particle
        energy density in units of number of electrons per unit energy as a
        `~astropy.units.Quantity` array or float.

    B : :class:`~astropy.units.Quantity` float instance, optional
        Isotropic magnetic field strength. Default: equipartition
        with CMB (3.24e-6 G)

    Other parameters
    ----------------
    log10gmin : float
        Base 10 logarithm of the minimum Lorentz factor for the electron
        distribution. Default is 4 (:math:`E_e ≈ 5` GeV).

    log10gmax : float
        Base 10 logarithm of the maximum Lorentz factor for the electron
        distribution. Default is 9 (:math:`E_e ≈ 510` TeV).

    ngamd : scalar
        Number of points per decade in energy for the electron energy and
        distribution arrays. Default is 100.
    """
    def __init__(self, particle_distribution, B=3.24e-6*u.G, **kwargs):
        self.particle_distribution = particle_distribution
        # check that the particle distribution returns particles per unit energy
        P = self.particle_distribution(1*u.TeV)
        validate_scalar('particle distribution', P, physical_type='differential energy')
        self.B = validate_scalar('B',B,physical_type='magnetic flux density')
        self.log10gmin = 4
        self.log10gmax = 9
        self.ngamd = 100
        self.__dict__.update(**kwargs)

    def spectrum(self, photon_energy):
        """Compute intrinsic synchrotron differential spectrum for energies in ``photon_energy``

        Compute synchrotron for random magnetic field according to approximation
        of Aharonian, Kelner, and Prosekin 2010, PhysRev D 82, 3002
        (`arXiv:1006.1045 <http://arxiv.org/abs/1006.1045>`_).

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` instance
            Photon energy array.
        """

        outspecene = _validate_ene(photon_energy)

        from scipy.special import cbrt

        def Gtilde(x):
            """
            AKP10 Eq. D7

            Factor ~2 performance gain in using cbrt(x)**n vs x**(n/3.)
            """
            gt1 = 1.808 * cbrt(x) / np.sqrt(1 + 3.4 * cbrt(x) ** 2.)
            gt2 = 1 + 2.210 * cbrt(x) ** 2. + 0.347 * cbrt(x) ** 4.
            gt3 = 1 + 1.353 * cbrt(x) ** 2. + 0.217 * cbrt(x) ** 4.
            return gt1 * (gt2 / gt3) * np.exp(-x)

        log.debug('calc_sy: Starting synchrotron computation with AKB2010...')

        # strip units, ensuring correct conversion
        # astropy units do not convert correctly for gyroradius calculation when using
        # cgs (SI is fine, see https://github.com/astropy/astropy/issues/1687)
        CS1_0 = np.sqrt(3) * e.value ** 3 * self.B.to('G').value
        CS1_1 = (2 * np.pi * m_e.cgs.value * c.cgs.value ** 2 *
                 hbar.cgs.value * outspecene.to('erg').value)
        CS1 = CS1_0/CS1_1

        # Critical energy, erg
        Ec = 3 * e.value * hbar.cgs.value * self.B.to('G').value * self._gam ** 2
        Ec /= 2 * (m_e * c).cgs.value

        EgEc = outspecene.to('erg').value / np.vstack(Ec)
        dNdE = CS1 * Gtilde(EgEc)
        # return units
        spec = trapz_loglog(np.vstack(self._nelec) * dNdE, self._gam, axis=0) / u.s / u.erg
        spec = spec.to('1/(s eV)')

        return spec

class InverseCompton(BaseElectron):
    """Inverse Compton emission from an electron population.

    Parameters
    ----------
    particle_distribution : function
        Particle distribution function, taking electron energies as a
        `~astropy.units.Quantity` array or float, and returning the particle
        energy density in units of number of electrons per unit energy as a
        `~astropy.units.Quantity` array or float.

    seed_photon_fields : string or iterable of strings (optional)
        A list of gray-body seed photon fields to use for IC calculation.
        Each of the items of the iterable can be:

        * A string equal to ``CMB`` (default), ``NIR``, or ``FIR``, for which
          radiation fields with temperatures of 2.72 K, 70 K, and 5000 K, and
          energy densities of 0.261, 0.5, and 1 eV/cm³ will be used

        * A list of length three (isotropic source) or four (anisotropic source)
          composed of:

            1. A name for the seed photon field
            2. Its temperature as a :class:`~astropy.units.Quantity` float
               instance.
            3. Its photon field energy density as a
               :class:`~astropy.units.Quantity` float instance. If the photon
               field energy density if set to 0, its blackbody energy density
               will be computed through the Stefan-Boltzman law.
            4. Optional: The angle between the seed photon direction and the scattered
               photon direction as a :class:`~astropy.units.Quantity` float
               instance.

    Other parameters
    ----------------
    log10gmin : float
        Base 10 logarithm of the minimum Lorentz factor for the electron
        distribution. Default is 4 (:math:`E_e ≈ 5` GeV).

    log10gmax : float
        Base 10 logarithm of the maximum Lorentz factor for the electron
        distribution. Default is 9 (:math:`E_e ≈ 510` TeV).

    ngamd : scalar
        Number of points per decade in energy for the electron energy and
        distribution arrays. Default is 300.
    """

    def __init__(self, particle_distribution, seed_photon_fields=['CMB',], **kwargs):
        self.particle_distribution = particle_distribution
        self.seed_photon_fields = seed_photon_fields
        self._process_input_seed()
        self.log10gmin = 4
        self.log10gmax = 9
        self.ngamd = 300
        self.__dict__.update(**kwargs)

    def _process_input_seed(self):
        """
        take input list of seed_photon_fields and fix them into usable format
        """

        Tcmb = 2.72548 * u.K  # 0.00057 K
        Tfir = 70 * u.K
        ufir = 0.2 * u.eV / u.cm ** 3
        Tnir = 5000 * u.K
        unir = 0.2 * u.eV / u.cm ** 3

        # Allow for seed_photon_fields definitions of the type 'CMB-NIR-FIR' or 'CMB'
        if type(self.seed_photon_fields) != list:
            self.seed_photon_fields = self.seed_photon_fields.split('-')

        self.seeduf = {}
        self.seedT = {}
        self.seedisotropic = {}
        self.seedtheta = {}
        for idx, inseed in enumerate(self.seed_photon_fields):
            if isinstance(inseed, six.string_types):
                if inseed == 'CMB':
                    self.seedT[inseed] = Tcmb
                    self.seeduf[inseed] = 1.0
                    self.seedisotropic[inseed] = True
                elif inseed == 'FIR':
                    self.seedT[inseed] = Tfir
                    self.seeduf[inseed] = (ufir / (ar * Tfir ** 4)).decompose()
                    self.seedisotropic[inseed] = True
                elif inseed == 'NIR':
                    self.seedT[inseed] = Tnir
                    self.seeduf[inseed] = (unir / (ar * Tnir ** 4)).decompose()
                    self.seedisotropic[inseed] = True
                else:
                    log.warning('Will not use seed {0} because it is not '
                                'CMB, FIR or NIR'.format(inseed))
                    raise TypeError
            elif type(inseed) == list and (len(inseed) == 3 or len(inseed) == 4):
                isotropic = len(inseed) == 3

                if isotropic:
                    name, T, uu = inseed
                    self.seedisotropic[name] = True
                else:
                    name, T, uu, theta = inseed
                    self.seedisotropic[name] = False
                    self.seedtheta[name] = validate_scalar('{0}-theta'.format(name),
                            theta, physical_type='angle')

                validate_scalar('{0}-T'.format(name), T, domain='positive',
                                physical_type='temperature')
                self.seed_photon_fields[idx] = name
                self.seedT[name] = T
                if uu == 0:
                    self.seeduf[name] = 1.0
                else:
                    # pressure has same physical type as energy density
                    validate_scalar('{0}-u'.format(name), uu,
                            domain='positive', physical_type='pressure')
                    self.seeduf[name] = (uu / (ar * T ** 4)).decompose()
            else:
                log.warning(
                    'Unable to process seed photon field: {0}'.format(inseed))
                raise TypeError

    @staticmethod
    def _iso_ic_on_planck(electron_energy, soft_photon_temperature, gamma_energy):
        """
        IC cross-section for isotropic interaction with a blackbody photon
        spectrum following Eq. 14 of Khangulyan, Aharonian, and Kelner 2014, ApJ
        783, 100 (`arXiv:1310.7971 <http://www.arxiv.org/abs/1310.7971>`_).

        `electron_energy` and `gamma_energy` are in units of m_ec^2
        `soft_photon_temperature` is in units of K
        """
        Ktomec2 = 1.6863699549e-10
        soft_photon_temperature *= Ktomec2

        def G34(x, a):
            """
            Eqs 20, 24, 25
            """
            alpha, a, beta, b, c = a
            pi26 = np.pi ** 2 / 6.0
            tmp = (1 + c * x) / (1 + pi26 * c * x)
            G = pi26 * tmp * np.exp(-x)
            tmp = 1 + b * x ** beta
            g = 1. / (a * x ** alpha / tmp + 1.)
            return G * g

        gamma_energy = np.vstack(gamma_energy)
        # Parameters from Eqs 26, 27
        a3 = [0.606, 0.443, 1.481, 0.540, 0.319]
        a4 = [0.461, 0.726, 1.457, 0.382, 6.620]
        z = gamma_energy / electron_energy
        x = z / (1 - z) / (4. * electron_energy * soft_photon_temperature)
        # Eq. 14
        cross_section = z ** 2 / (2 * (1 - z)) * G34(x, a3) + G34(x, a4)
        tmp = (soft_photon_temperature / electron_energy) ** 2
        # r0 = (e**2 / m_e / c**2).to('cm')
        # (2 * r0 ** 2 * m_e ** 3 * c ** 4 / (pi * hbar ** 3)).cgs
        tmp *= 2.6318735743809104e+16
        cross_section = tmp * cross_section
        cc = ((gamma_energy < electron_energy) * (electron_energy > 1))
        return np.where(cc, cross_section,
                        np.zeros_like(cross_section))

    @staticmethod
    def _ani_ic_on_planck(electron_energy, soft_photon_temperature, gamma_energy, theta):
        """
        IC cross-section for anisotropic interaction with a blackbody photon
        spectrum following Eq. 11 of Khangulyan, Aharonian, and Kelner 2014, ApJ
        783, 100 (`arXiv:1310.7971 <http://www.arxiv.org/abs/1310.7971>`_).

        `electron_energy` and `gamma_energy` are in units of m_ec^2
        `soft_photon_temperature` is in units of K
        `theta` is in radians
        """
        Ktomec2 = 1.6863699549e-10
        soft_photon_temperature *= Ktomec2

        def G12(x, a):
            """
            Eqs 20, 24, 25
            """
            alpha, a, beta, b = a
            pi26 = np.pi ** 2 / 6.0
            G = (pi26 + x) * np.exp(-x)
            tmp = 1 + b * x ** beta
            g = 1. / (a * x ** alpha / tmp + 1.)
            return G * g

        gamma_energy = np.vstack(gamma_energy)
        # Parameters from Eqs 21, 22
        a1 = [0.857, 0.153, 1.840, 0.254]
        a2 = [0.691, 1.330, 1.668, 0.534]
        z = gamma_energy / electron_energy
        ttheta = 2. * electron_energy * soft_photon_temperature * (1. - np.cos(theta))
        x = z / (1 - z) / ttheta
        # Eq. 11
        cross_section = z ** 2 / (2 * (1 - z)) * G12(x, a1) + G12(x, a2)
        tmp = (soft_photon_temperature / electron_energy) ** 2
        # r0 = (e**2 / m_e / c**2).to('cm')
        # (2 * r0 ** 2 * m_e ** 3 * c ** 4 / (pi * hbar ** 3)).cgs
        tmp *= 2.6318735743809104e+16
        cross_section = tmp * cross_section
        cc = ((gamma_energy < electron_energy) * (electron_energy > 1))
        return np.where(cc, cross_section,
                        np.zeros_like(cross_section))

    def _calc_specic(self, seed, outspecene):
        log.debug(
            '_calc_specic: Computing IC on {0} seed photons...'.format(seed))

        uf = self.seeduf[seed]
        T = self.seedT[seed]

        Eph = (outspecene / mec2).decompose().value
        # Catch numpy RuntimeWarnings of overflowing exp (which are then discarded anyway)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.seedisotropic[seed]:
                gamint = self._iso_ic_on_planck(self._gam, T.to('K').value, Eph)
            else:
                theta = self.seedtheta[seed].to('rad').value
                gamint = self._ani_ic_on_planck(self._gam, T.to('K').value, Eph, theta)
            lum = uf * Eph * trapz_loglog(self._nelec * gamint, self._gam)
        lum *= u.Unit('1/s')

        return lum / outspecene  # return differential spectrum in 1/s/eV

    def spectrum(self,photon_energy):
        """Compute differential IC spectrum for energies in ``photon_energy``.

        Compute IC spectrum using IC cross-section for isotropic interaction
        with a blackbody photon spectrum following Khangulyan, Aharonian, and
        Kelner 2014, ApJ 783, 100 (`arXiv:1310.7971
        <http://www.arxiv.org/abs/1310.7971>`_).

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` instance
            Photon energy array.
        """
        outspecene = _validate_ene(photon_energy)

        self.specic = np.zeros(len(outspecene)) * u.Unit('1/(s eV)')

        for seed in self.seed_photon_fields:
            # Call actual computation, detached to allow changes in subclasses
            self.specic += self._calc_specic(seed,outspecene).to('1/(s eV)')

        self.specic = self.specic.to('1/(s eV)')

        return self.specic


class Bremsstrahlung(BaseElectron):
    """
    Bremsstrahlung radiation on a completely ionised gas.

    Following Baring, Ellison, Reynolds, Grenier, and Goret 1999, ApJ 513, 311.

    The default weights are assuming a completely ionised target gas with ISM
    abundances. If pure electron-electron bremsstrahlung is desired, ``n0`` can
    be set to the electron density, ``weight_ep`` to 0 and ``weight_ee`` to 1.

    Parameters
    ----------
    n0 : :class:`~astropy.units.Quantity` float
        Total ion number density.

    Other parameters
    ----------------
    weight_ee : float
        Weight of electron-electron bremsstrahlung. Defined as :math:`\sum_i Z_i
        X_i`, default is 1.088.
    weight_ep : float
        Weight of electron-proton bremsstrahlung. Defined as :math:`\sum_i Z_i^2
        X_i`, default is 1.263.
    """

    def __init__(self, particle_distribution, n0 = 1 / u.cm**3, **kwargs):
        self.particle_distribution = particle_distribution
        self.n0 = n0
        self.log10gmin = 4
        self.log10gmax = 9
        self.ngamd = 300
        # compute ee and ep weights from H and He abundances in ISM assumin ionized medium
        Y = np.array([1.,9.59e-2])
        Z = np.array([1,2])
        N = np.sum(Y)
        X = Y/N
        self.weight_ee = np.sum(Z*X)
        self.weight_ep = np.sum(Z**2*X)
        self.__dict__.update(**kwargs)

    @staticmethod
    def _sigma_1(gam, eps):
        """
        gam and eps in units of m_e c^2
        Eq. A2 of Baring et al. (1999)
        Return in units of cm2 / mec2
        """
        s1 = 4 * r0**2 * alpha / eps / mec2_unit
        s2 = 1 + (1./3. - eps/gam) * (1 - eps/gam)
        s3 = np.log(2 * gam * (gam - eps) / eps) - 1./2.
        s3[np.where(gam < eps)] = 0.0
        return s1 * s2 * s3

    @staticmethod
    def _sigma_2(gam, eps):
        """
        gam and eps in units of m_e c^2
        Eq. A3 of Baring et al. (1999)
        Return in units of cm2 / mec2
        """
        s0 = r0**2 * alpha / (3 * eps) / mec2_unit

        s1_1 = 16 * (1 - eps + eps**2) * np.log(gam / eps)
        s1_2 = -1 / eps**2 + 3 / eps - 4 - 4 * eps - 8 * eps**2
        s1_3 = -2 * (1 - 2 * eps) * np.log(1 - 2 * eps)
        s1_4 = 1 / (4 * eps**3) - 1 / (2 * eps**2) + 3 / eps - 2 + 4 * eps
        s1 = s1_1 + s1_2 + s1_3 * s1_4

        s2_1 = 2 / eps
        s2_2 = (4 - 1 / eps + 1 / (4 * eps**2)) * np.log(2 * gam)
        s2_3 = -2 + 2 / eps - 5 / (8 * eps**2)
        s2 = s2_1 * (s2_2 + s2_3)

        return s0 * np.where(eps <= 0.5, s1, s2) * heaviside(gam - eps)

    def _sigma_ee_rel(self,gam,eps):
        """
        Eq. A1, A4 of Baring et al. (1999)
        Use for Ee > 2 MeV
        """
        A = 1 - 8 / 3 * (gam - 1)**0.2 / (gam + 1) * (eps / gam)**(1./3.)

        return (self._sigma_1(gam,eps) + self._sigma_2(gam,eps)) * A

    @staticmethod
    def _F(x,gam):
        """
        Eqs. A6, A7 of Baring et al. (1999)
        """
        beta = np.sqrt(1 - gam**-2)
        B = 1 + 0.5 * (gam**2 - 1)
        C = 10 * x * gam * beta * (2 + gam * beta)
        C /= 1 + x**2 * (gam**2 - 1)

        F_1 = (17 - 3 * x**2 / (2 - x)**2 - C) * np.sqrt(1 - x)
        F_2 = 12 * (2 -x) - 7 * x**2 / (2 - x) - 3 * x**4 / (2 - x)**3
        F_3 = np.log((1 + np.sqrt(1 - x)) / np.sqrt(x))

        return B * F_1 + F_2 * F_3

    def _sigma_ee_nonrel(self,gam,eps):
        """
        Eq. A5 of Baring et al. (1999)
        Use for Ee < 2 MeV
        """
        s0 = 4 * r0**2 * alpha / (15 * eps)
        x = 4 * eps / (gam**2 - 1)
        sigma_nonrel = s0 * self._F(x,gam)
        sigma_nonrel[np.where(eps >= 0.25*(gam**2 - 1.))] = 0.0
        sigma_nonrel[np.where(gam*np.ones_like(eps) < 1.0)] = 0.0
        return sigma_nonrel / mec2_unit

    def _sigma_ee(self,gam,Eph):
        eps = (Eph / mec2).decompose().value
        # initialize shape and units of cross section
        sigma = np.zeros_like(gam*eps) * u.Unit(u.cm**2 / Eph.unit)
        gam_trans = (2 * u.MeV / mec2).decompose().value
        # Non relativistic below 2 MeV
        if np.any(gam <= gam_trans):
            nr_matrix = np.where(gam * np.ones_like(gam*eps) <= gam_trans)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sigma[nr_matrix] = self._sigma_ee_nonrel(gam, eps)[nr_matrix]
        # Relativistic above 2 MeV
        if np.any(gam > gam_trans):
            rel_matrix = np.where(gam * np.ones_like(gam*eps) > gam_trans)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sigma[rel_matrix] = self._sigma_ee_rel(gam, eps)[rel_matrix]

        return sigma.to(u.cm**2 / Eph.unit)

    def _sigma_ep(self,gam,eps):
        """
        Using sigma_1 only applies to the ultrarelativistic regime.
        Eph > 10 MeV
        ToDo: add complete e-p cross-section
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self._sigma_1(gam,eps)

    def _emiss_ee(self,Eph):
        """
        Electron-electron bremsstrahlung emissivity per unit photon energy
        """
        if self.weight_ee == 0.0:
            return np.zeros_like(Eph)

        gam = np.vstack(self._gam)
        # compute integral with electron distribution
        emiss = c.cgs * trapz_loglog(np.vstack(self._nelec) * self._sigma_ee(gam,Eph),
                                     self._gam, axis=0)
        return emiss

    def _emiss_ep(self,Eph):
        """
        Electron-proton bremsstrahlung emissivity per unit photon energy
        """
        if self.weight_ep == 0.0:
            return np.zeros_like(Eph)

        gam = np.vstack(self._gam)
        eps = (Eph / mec2).decompose().value
        # compute integral with electron distribution
        emiss = c.cgs * trapz_loglog(np.vstack(self._nelec) * self._sigma_1(gam,eps),
                                     self._gam, axis=0).to(u.cm**2 / Eph.unit)
        return emiss

    def spectrum(self,photon_energy):
        """Compute differential bremsstrahlung spectrum for energies in ``photon_energy``.

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` instance
            Photon energy array.
        """

        Eph = _validate_ene(photon_energy)

        spec = self.n0 * (self.weight_ee * self._emiss_ee(Eph)
                                        + self.weight_ep * self._emiss_ep(Eph))

        return spec


class PionDecay(BaseRadiative):
    r"""Pion decay gamma-ray emission from a proton population.

    Compute gamma-ray spectrum arising from the interaction of a relativistic
    proton distribution with stationary target protons.

    Parameters
    ----------
    particle_distribution : function
        Particle distribution function, taking proton energies as a
        `~astropy.units.Quantity` array or float, and returning the particle
        energy density in units of number of protons per unit energy as a
        `~astropy.units.Quantity` array or float.

    nh : `~astropy.units.Quantity`
        Number density of the target protons. Default is :math:`1 \mathrm{cm}^{-3}`.

    nuclear_enhancement : bool
        Whether to apply the energy-dependent nuclear enhancement factor
        considering a target gas with local ISM abundances. See Section IV of
        Kafexhiu et al. (2014) for details. Here the proton-nucleus inelastic
        cross section of Sihver et al. (1993, PhysRevC 47, 1225) is used.

    Other parameters
    ----------------
    log10Epmin : float
        Base 10 logarithm of the minimum proton energy for the proton
        distribution. Default is 0.086, the dynamical threshold for pion
        production in pp interactions. (:math:`E_p ≈ 1.22` GeV)

    log10Epmax : float
        Base 10 logarithm of the minimum proton energy for the proton
        distribution. Default is 7 (:math:`E_p = 10` PeV).

    nEpd : scalar
        Number of points per decade in energy for the proton energy and
        distribution arrays. Default is 100.

    hiEmodel : str
        Monte Carlo model to use for computation of high-energy differential
        cross section. Can be one of ``Geant4``, ``Pythia8``, ``SIBYLL``, or
        ``QGSJET``. See Kafexhiu et al. (2014) for details. Default is
        ``Pythia8``.

    useLUT : bool
        Whether to use a lookup table for the differential cross section. The
        only lookup table packaged with gammafit is for the Pythia 8 model and
        ISM nuclear enhancement factor.

    References
    ----------
    Kafexhiu, E., Aharonian, F., Taylor, A.~M., and Vila, G.~S.\ 2014,
    `arXiv:1406.7369 <http://www.arxiv.org/abs/1406.7369>`_.
    """

    def __init__(self, particle_distribution, nh = 1.0 / u.cm**3,
            nuclear_enhancement = True, **kwargs):
        self.particle_distribution = particle_distribution
        self.nh = validate_scalar('nh', nh, physical_type='number density')
        self.nuclear_enhancement = nuclear_enhancement
        self.useLUT = True
        self.hiEmodel = 'Pythia8'
        self.log10Epmin = np.log10(self._m_p + self._Tth) # Threshold energy ~1.22 GeV
        self.log10Epmax = np.log10(10.e6) # 10 PeV
        self.nEpd = 100
        self.__dict__.update(**kwargs)


    # define model parameters from tables
    #
    # Table IV
    _a = {}
    _a['Geant4']  = [0.728, 0.596,  0.491, 0.2503, 0.117] # Tp > 5
    _a['Pythia8'] = [0.652, 0.0016, 0.488, 0.1928, 0.483] # Tp > 50
    _a['SIBYLL']  = [5.436, 0.254,  0.072, 0.075,  0.166] # Tp > 100
    _a['QGSJET']  = [0.908, 0.0009, 6.089, 0.176,  0.448] # Tp > 100
    #
    # table V data
    # note that np.nan indicate that functions of Tp are needed and are defined
    # as need in function F
    # parameter order is lambda, alpha, beta, gamma
    _F_mp = {}
    _F_mp['ExpData']  = [1.0,  1.0, np.nan, 0.0]    # Tth  <= Tp <= 1.0
    _F_mp['Geant4_0'] = [3.0,  1.0, np.nan, np.nan] # 1.0  <  Tp <= 4.0
    _F_mp['Geant4_1'] = [3.0,  1.0, np.nan, np.nan] # 4.0  <  Tp <= 20.0
    _F_mp['Geant4_2'] = [3.0,  0.5, 4.2,    1.0]    # 20.0 <  Tp <= 100
    _F_mp['Geant4']   = [3.0,  0.5, 4.9,    1.0]    # Tp > 100
    _F_mp['Pythia8']  = [3.5,  0.5, 4.0,    1.0]    # Tp > 50
    _F_mp['SIBYLL']   = [3.55, 0.5, 3.6,    1.0]    # Tp > 100
    _F_mp['QGSJET']   = [3.55, 0.5, 4.5,    1.0]    # Tp > 100
    #
    # Table VII
    _b = {}
    _b['Geant4_0'] = [9.53,  0.52,   0.054]   # 1 <= Tp < 5
    _b['Geant4']   = [9.13,  0.35,   9.7e-3]  # Tp >= 5
    _b['Pythia8']  = [9.06,  0.3795, 0.01105] # Tp >  50
    _b['SIBYLL']   = [10.77, 0.412,  0.01264] # Tp >  100
    _b['QGSJET']   = [13.16, 0.4419, 0.01439] # Tp >  100

    # energy at which each of the hiE models start being valid
    _Etrans = {'Pythia8':50, 'SIBYLL':100, 'QGSJET':100, 'Geant4': 100}
    #
    _m_p = (m_p * c ** 2).to('GeV').value
    _m_pi = 0.1349766  # GeV/c2
    _Tth = 0.27966184


    def _sigma_inel(self, Tp):
        """
        Inelastic cross-section for p-p interaction. KATV14 Eq. 1

        Parameters
        ----------
        Tp : float
            Kinetic energy of proton (i.e. Ep - m_p*c**2) [GeV]

        Returns
        -------
        sigma_inel : float
            Inelastic cross-section for p-p interaction [1/cm2].

        """
        L = np.log(Tp/self._Tth)
        sigma = 30.7 - 0.96 * L + 0.18 * L ** 2
        sigma *= (1 - (self._Tth / Tp) ** 1.9) ** 3
        return sigma * 1e-27  # convert from mbarn to cm-2

    def _sigma_pi_loE(self,Tp):
        """
        inclusive cross section for Tth < Tp < 2 GeV
        Fit from experimental data
        """
        m_p = self._m_p
        m_pi = self._m_pi
        Mres = 1.1883 # GeV
        Gres = 0.2264 # GeV
        s = 2 * m_p * (Tp + 2 * m_p) # center of mass energy
        gamma = np.sqrt(Mres**2 * (Mres**2 + Gres**2))
        K = np.sqrt(8) * Mres * Gres * gamma
        K /= np.pi * np.sqrt(Mres**2 + gamma)

        fBW = m_p * K
        fBW /= ((np.sqrt(s) - m_p)**2 - Mres**2) ** 2 + Mres**2 * Gres**2

        mu = np.sqrt((s - m_pi**2 - 4 * m_p**2)**2 - 16 * m_pi**2 * m_p**2)
        mu /= 2 * m_pi * np.sqrt(s)

        sigma0 = 7.66e-3 # mb

        sigma1pi = sigma0 * mu**1.95 * (1 + mu + mu**5) * fBW**1.86

        # two pion production
        sigma2pi = 5.7 # mb
        sigma2pi /= 1 + np.exp(-9.3*(Tp - 1.4))

        E2pith = 0.56 # GeV
        sigma2pi[np.where(Tp < E2pith)] = 0.

        return (sigma1pi + sigma2pi) * 1e-27 # return in cm-2

    def _sigma_pi_midE(self,Tp):
        """
        Geant 4.10.0 model for 2 GeV < Tp < 5 GeV
        """
        m_p = self._m_p
        m_pi = self._m_pi
        Qp = (Tp - self._Tth) / m_p
        multip = -6e-3 + 0.237 * Qp - 0.023 * Qp**2
        return self._sigma_inel(Tp) * multip

    def _sigma_pi_hiE(self,Tp,a):
        """
        General expression for Tp > 5 GeV (Eq 7)
        """
        m_p = self._m_p
        m_pi = self._m_pi
        csip = (Tp - 3.0) / m_p
        m1 = a[0] * csip ** a[3] * (1 + np.exp(-a[1] * csip ** a[4]))
        m2 = 1 - np.exp(-a[2] * csip ** 0.25)
        multip = m1 * m2
        return self._sigma_inel(Tp) * multip


    def _sigma_pi(self,Tp):
        sigma = np.zeros_like(Tp)

        # for E<2GeV
        idx1 = np.where(Tp < 2.0)
        sigma[idx1] = self._sigma_pi_loE(Tp[idx1])
        # for 2GeV<=E<5GeV
        idx2 = np.where((Tp >= 2.0) * (Tp < 5.0))
        sigma[idx2] = self._sigma_pi_midE(Tp[idx2])
        # for 5GeV<=E<Etrans
        idx3 = np.where((Tp >= 5.0) * (Tp < self._Etrans[self.hiEmodel]))
        sigma[idx3] = self._sigma_pi_hiE(Tp[idx3], self._a['Geant4'])
        # for E>=Etrans
        idx4 = np.where((Tp >= self._Etrans[self.hiEmodel]))
        sigma[idx4] = self._sigma_pi_hiE(Tp[idx4],self._a[self.hiEmodel])

        return sigma

    def _b_params(self,Tp):
        b0 = 5.9
        hiE = np.where(Tp >= 1.0)
        TphiE = Tp[hiE]
        b1 = np.zeros(TphiE.size)
        b2 = np.zeros(TphiE.size)
        b3 = np.zeros(TphiE.size)

        idx = np.where(TphiE < 5.0)
        b1[idx], b2[idx], b3[idx] = self._b['Geant4_0']

        idx = np.where(TphiE >= 5.0)
        b1[idx], b2[idx], b3[idx] = self._b['Geant4']

        idx = np.where(TphiE >= self._Etrans[self.hiEmodel])
        b1[idx], b2[idx], b3[idx] = self._b[self.hiEmodel]

        return b0, b1, b2, b3

    def _calc_coll_props(self,Tp):
        m_p = self._m_p
        m_pi = self._m_pi
        # Eq 10
        s = 2 * m_p * (Tp + 2 * m_p) # center of mass energy
        EpiCM = (s - 4 * m_p**2 + m_pi**2) / (2 * np.sqrt(s))
        PpiCM = np.sqrt(EpiCM ** 2 - m_pi **2)
        gCM = (Tp + 2 * m_p)/np.sqrt(s)
        betaCM = np.sqrt(1 - gCM ** -2)
        EpimaxLAB = gCM * (EpiCM + PpiCM * betaCM)
        gpiLAB = EpimaxLAB / m_pi
        betapiLAB = np.sqrt(1 - gpiLAB ** -2)
        Egmax = (m_pi / 2) * gpiLAB * ( 1 + betapiLAB)

        return Egmax, EpimaxLAB

    def _Amax(self,Tp):
        m_p = self._m_p
        m_pi = self._m_pi
        loE = np.where(Tp<1.0)
        hiE = np.where(Tp>=1.0)

        Amax = np.zeros(Tp.size)

        b = self._b_params(Tp)

        Egmax, EpimaxLAB = self._calc_coll_props(Tp)
        Amax[loE] = b[0] * self._sigma_pi(Tp[loE]) / EpimaxLAB[loE]
        thetap = Tp / m_p
        Amax[hiE] = (b[1] * thetap[hiE] ** -b[2] *
                     np.exp(b[3]*np.log(thetap[hiE])**2) *
                     self._sigma_pi(Tp[hiE]) / m_p)

        return Amax

    def _F_func(self,Tp,Egamma,modelparams):
        lamb, alpha, beta, gamma = modelparams
        m_p = self._m_p
        m_pi = self._m_pi
        # Eq 9
        Egmax, EpimaxLAB = self._calc_coll_props(Tp)
        Yg = Egamma + m_pi ** 2 / (4 * Egamma)
        Ygmax = Egmax + m_pi ** 2 / (4 * Egmax)
        Xg = (Yg - m_pi)/(Ygmax - m_pi)
        # zero out invalid fields (Egamma > Egmax -> Xg > 1)
        Xg[np.where(Xg > 1)] = 1.0
        # Eq 11
        C = lamb * m_pi / Ygmax
        F = (1 - Xg ** alpha) ** beta
        F /= (1 + Xg / C) ** gamma
        #
        return F

    def _kappa(self,Tp):
        thetap = Tp / self._m_p
        return 3.29 - thetap ** -1.5 / 5.

    def _mu(self,Tp):
        q = (Tp - 1.0)/self._m_p
        x = 5./4.
        return x * q ** x * np.exp(-x*q)

    def _F(self,Tp,Egamma):
        F = np.zeros_like(Tp)
# below Tth
        F[np.where(Tp < self._Tth)] = 0.0
# Tth <= E <= 1GeV: Experimental data
        idx = np.where((Tp >= self._Tth) * (Tp <= 1.0))
        if idx[0].size > 0:
            kappa = self._kappa(Tp[idx])
            mp = self._F_mp['ExpData']
            mp[2] = kappa
            F[idx] = self._F_func(Tp[idx], Egamma, mp)
# 1GeV < Tp < 4 GeV: Geant4 model 0
        idx = np.where((Tp > 1.0) * (Tp <= 4.0))
        if idx[0].size > 0:
            mp = self._F_mp['Geant4_0']
            mu = self._mu(Tp[idx])
            mp[2] = mu + 2.45
            mp[3] = mu + 1.45
            F[idx] = self._F_func(Tp[idx], Egamma, mp)
# 4 GeV < Tp < 20 GeV
        idx = np.where((Tp > 4.0) * (Tp <= 20.0))
        if idx[0].size > 0:
            mp = self._F_mp['Geant4_1']
            mu = self._mu(Tp[idx])
            mp[2] = 1.5 * mu + 4.95
            mp[3] = mu + 1.50
            F[idx] = self._F_func(Tp[idx], Egamma, mp)
# 20 GeV < Tp < 100 GeV
        idx = np.where((Tp > 20.0) * (Tp <= 100.0))
        if idx[0].size > 0:
            mp = self._F_mp['Geant4_2']
            F[idx] = self._F_func(Tp[idx], Egamma, mp)
# Tp > Etrans
        idx = np.where(Tp > self._Etrans[self.hiEmodel])
        if idx[0].size > 0:
            mp = self._F_mp[self.hiEmodel]
            F[idx] = self._F_func(Tp[idx], Egamma, mp)

        return F

    def _diffsigma(self,Ep,Egamma):
        """
        Differential cross section

        dsigma/dEg = Amax(Tp) * F(Tp,Egamma)
        """
        Tp = Ep - self._m_p

        diffsigma = self._Amax(Tp) * self._F(Tp,Egamma)

        if self.nuclear_enhancement:
            diffsigma *= self._nuclear_factor(Tp)

        return diffsigma

    def _nuclear_factor(self,Tp):
        """
        Compute nuclear enhancement factor
        """
        sigmaRpp = 10 * np.pi * 1e-27
        sigmainel = self._sigma_inel(Tp)
        sigmainel0 = self._sigma_inel(1e3) # at 1e3 GeV
        f = sigmainel / sigmainel0
        f2 = np.where(f > 1, f, 1.0)
        G = 1.0 + np.log(f2)
        # epsilon factors computed from Eqs 21 to 23 with local ISM abundances
        epsC = 1.37
        eps1 = 0.29
        eps2 = 0.1

        epstotal = np.where(Tp > self._Tth,
                            epsC + (eps1 + eps2) * sigmaRpp * G / sigmainel,
                            0.0)

        if np.any(Tp < 1.0):
            # nuclear enhancement factor diverges towards Tp = Tth, fix Tp<1 to eps(1.0) = 1.91
            loE=np.where((Tp > self._Tth) * (Tp < 1.0))
            epstotal[loE] = 1.9141

        return epstotal

    @property
    def _Ep(self):
        """ Proton energy array in GeV
        """
        return np.logspace(self.log10Epmin,self.log10Epmax,
                           self.nEpd * (self.log10Epmax-self.log10Epmin))

    @property
    def _J(self):
        """ Particles per unit proton energy in particles per GeV
        """
        pd = self.particle_distribution(self._Ep * u.GeV)
        return pd.to('1/GeV').value

    @property
    def Wp(self):
        """Total energy in protons
        """
        Wp = trapz_loglog(self._Ep * self._J, self._Ep) * u.GeV
        return Wp.to('erg')

    def spectrum(self,photon_energy):
        """
        Compute differential spectrum from pp interactions using the parametrization of
        Kafexhiu, E., Aharonian, F., Taylor, A.~M., and Vila, G.~S.\ 2014,
        `arXiv:1406.7369 <http://www.arxiv.org/abs/1406.7369>`_.

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` instance
            Photon energy array.
        """

        # Load LUT if available, otherwise use self._diffsigma
        if self.useLUT:
            LUT_base = 'PionDecayKafexhiu14_LUT_'
            if self.nuclear_enhancement:
                LUT_base += 'NucEnh_'
            LUT_fname = LUT_base+'{0}.npz'.format(self.hiEmodel)
            try:
                filename = get_pkg_data_filename(os.path.join('data',LUT_fname))
                self.diffsigma = LookupTable(filename)
            except IOError:
                warnings.warn('LUT {0} not found, reverting to useLUT = False'.format(LUT_fname))
                self.diffsigma = self._diffsigma
                self.useLUT = False
        else:
            self.diffsigma = self._diffsigma

        Egamma = _validate_ene(photon_energy).to('GeV')
        Ep = self._Ep * u.GeV
        J = self._J * u.Unit('1/GeV')

        specpp = []
        for Eg in Egamma:
            diffsigma = self.diffsigma(Ep.value,Eg.value) * u.Unit('cm2/GeV')
            specpp.append(trapz_loglog(diffsigma * J, Ep))

        self.specpp = u.Quantity(specpp)

        self.specpp *= self.nh * c.cgs

        return self.specpp.to('1/(s eV)')

heaviside = lambda x: (np.sign(x) + 1) / 2.

class PionDecayKelner06(BaseRadiative):
    r"""Pion decay gamma-ray emission from a proton population.

    Compute gamma-ray spectrum arising from the interaction of a relativistic
    proton distribution with stationary target protons.

    Parameters
    ----------
    particle_distribution : function
        Particle distribution function, taking proton energies as a
        `~astropy.units.Quantity` array or float, and returning the particle
        energy density in units of number of protons per unit energy as a
        `~astropy.units.Quantity` array or float.

    nh : `~astropy.units.Quantity`
        Number density of the target protons. Default is :math:`1 cm^{-3}`.

    useLUT : bool
        Use precomputed lookup tables for the differential cross section.
        Default is False.

    Other parameters
    ----------------
    Etrans : `~astropy.units.Quantity`
        For photon energies below ``Etrans``, the delta-functional approximation
        is used for the spectral calculation, and the full calculation is used
        at higher energies. Default is 0.1 TeV.

    References
    ----------
    Kelner, S.R., Aharonian, F.A., and Bugayov, V.V., 2006 PhysRevD 74, 034018
    (`arXiv:astro-ph/0606058 <http://www.arxiv.org/abs/astro-ph/0606058>`_).

    """

    def __init__(self, particle_distribution, nh = 1.0 / u.cm**3, **kwargs):
        self.particle_distribution = particle_distribution
        self.nh = validate_scalar('nh', nh, physical_type='number density')

        self.__dict__.update(**kwargs)

    def _particle_distribution(self,E):
        return self.particle_distribution(E*u.TeV).to('1/TeV').value

    def _Fgamma(self, x, Ep):
        """
        KAB06 Eq.58

        Note: Quantities are not used in this function

        Parameters
        ----------
        x : float
            Egamma/Eprot
        Ep : float
            Eprot [TeV]
        """
        L = np.log(Ep)
        B = 1.30 + 0.14 * L + 0.011 * L ** 2  # Eq59
        beta = (1.79 + 0.11 * L + 0.008 * L ** 2) ** -1  # Eq60
        k = (0.801 + 0.049 * L + 0.014 * L ** 2) ** -1  # Eq61
        xb = x ** beta

        F1 = B * (np.log(x) / x) * ((1 - xb) / (1 + k * xb * (1 - xb))) ** 4
        F2 = 1. / np.log(x) - (4 * beta * xb) / (1 - xb) - (
            4 * k * beta * xb * (1 - 2 * xb)) / (1 + k * xb * (1 - xb))

        return F1 * F2

    def _sigma_inel(self, Ep):
        """
        Inelastic cross-section for p-p interaction. KAB06 Eq. 73, 79

        Note: Quantities are not used in this function

        Parameters
        ----------
        Ep : float
            Eprot [TeV]

        Returns
        -------
        sigma_inel : float
            Inelastic cross-section for p-p interaction [1/cm2].

        """
        L = np.log(Ep)
        sigma = 34.3 + 1.88 * L + 0.25 * L ** 2
        if Ep <= 0.1:
            Eth = 1.22e-3
            sigma *= (1 - (Eth / Ep) ** 4) ** 2 * heaviside(Ep - Eth)
        return sigma * 1e-27  # convert from mbarn to cm2

    def _photon_integrand(self, x, Egamma):
        """
        Integrand of Eq. 72
        """
        try:
            return self._sigma_inel(Egamma / x) * self._particle_distribution((Egamma / x)) \
                * self._Fgamma(x, Egamma / x) / x
        except ZeroDivisionError:
            return np.nan

    def _calc_specpp_hiE(self, Egamma):
        """
        Spectrum computed as in Eq. 42 for Egamma >= 0.1 TeV
        """
        # Fixed quad with n=40 is about 15 times faster and is always within
        # 0.5% of the result of adaptive quad for Egamma>0.1
        # WARNING: It also produces artifacts for steep distributions (e.g.
        # Maxwellian) at ~500 GeV. Reverting to adaptative quadrature
        # from scipy.integrate import fixed_quad
        # result=c*fixed_quad(self._photon_integrand, 0., 1., args = [Egamma,
        # ], n = 40)[0]
        from scipy.integrate import quad
        Egamma = Egamma.to('TeV').value
        specpp = c.cgs.value * quad(
            self._photon_integrand, 0., 1., args=Egamma,
            epsrel=1e-3, epsabs=0)[0]

        return specpp * u.Unit('1/(s TeV)')

    # variables for delta integrand
    _c = c.cgs.value
    _Kpi = 0.17
    _mp = (m_p * c ** 2).to('TeV').value
    _m_pi = 1.349766e-4  # TeV/c2

    def _delta_integrand(self, Epi):
        Ep0 = self._mp + Epi / self._Kpi
        qpi = self._c * \
            (self.nhat / self._Kpi) * self._sigma_inel(Ep0) * self._particle_distribution(Ep0)
        return qpi / np.sqrt(Epi ** 2 + self._m_pi ** 2)

    def _calc_specpp_loE(self, Egamma):
        """
        Delta-functional approximation for low energies Egamma < 0.1 TeV
        """
        from scipy.integrate import quad
        Egamma = Egamma.to('TeV').value
        Epimin = Egamma + self._m_pi ** 2 / (4 * Egamma)

        result = 2 * quad(self._delta_integrand, Epimin, np.inf, epsrel=1e-3,
                          epsabs=0)[0]

        return result * u.Unit('1/(s TeV)')

    @property
    def Wp(self):
        """Total energy in protons above 1.22 GeV threshold (erg).
        """
        from scipy.integrate import quad
        Eth = 1.22e-3

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            Wp = quad(lambda x: x * self._particle_distribution(x), Eth, np.Inf)[0]

        return (Wp * u.TeV).to('erg')

    def spectrum(self,photon_energy):
        """
        Compute differential spectrum from pp interactions using Eq.71 and Eq.58 of
        Kelner, S.R., Aharonian, F.A., and Bugayov, V.V., 2006 PhysRevD 74, 034018
        (`arXiv:astro-ph/0606058 <http://www.arxiv.org/abs/astro-ph/0606058>`_).

        Parameters
        ----------
        photon_energy : :class:`~astropy.units.Quantity` instance
            Photon energy array.
        """

        outspecene = _validate_ene(photon_energy)

        if not hasattr(self, 'Etrans'):
            # Energy at which we change from delta functional to accurate
            # calculation
            self.Etrans = 0.1 * u.TeV
        else:
            validate_scalar('Etrans', self.Etrans,
                    domain='positive', physical_type='energy')

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.nhat = 1.  # initial value, works for index~2.1
            if np.any(outspecene < self.Etrans) and np.any(outspecene >= self.Etrans):
                # compute value of nhat so that delta functional matches accurate
                # calculation at 0.1TeV
                full = self._calc_specpp_hiE(self.Etrans)
                delta = self._calc_specpp_loE(self.Etrans)
                self.nhat *= (full / delta).decompose().value

            self.specpp = np.zeros(len(outspecene)) * u.Unit('1/(s TeV)')

            for i, Egamma in enumerate(outspecene):
                if Egamma >= self.Etrans:
                    self.specpp[i] = self._calc_specpp_hiE(Egamma)
                else:
                    self.specpp[i] = self._calc_specpp_loE(Egamma)

        density_factor = (self.nh / (1 * u.Unit('1/cm3'))).decompose().value

        return density_factor * self.specpp.to('1/(s eV)')

class LookupTable(object):
    """
    Helper class for two-dimensional look up table

    Lookup table should be saved as an npz file with numpy.savez or
    numpy.savez_compressed. The file should have three arrays:

    * X: log10(x)
    * Y: log10(y)
    * lut: log10(z)

    The instantiated object can be called with arguments (x,y), and the
    interpolated value of z will be returned. The interpolation is done through
    a cubic spline in semi-logarithmic space.
    """
    def __init__(self,filename):
        from scipy.interpolate import RectBivariateSpline
        f_lut = np.load(filename)
        X = f_lut.f.X
        Y = f_lut.f.Y
        lut = f_lut.f.lut
        self.int_lut = RectBivariateSpline(X, Y, 10**lut, kx=3, ky=3, s=0)

    def __call__(self,X,Y):
        return self.int_lut(np.log10(X),np.log10(Y)).flatten()

def _calc_lut_pp(args):
    epr, eph, hiEmodel, nuc = args
    #print('Computing diffsigma for Egamma = {0}...'.format(eph))
    from astropy import constants as const
    from .radiative import PionDecay
    from .models import PowerLaw
    pl = PowerLaw(1/u.eV,1*u.TeV,0.0)
    pp = PionDecayKafexhiu14(pl,hiEmodel=hiEmodel,nuclear_enhancement=nuc)

    diffsigma = pp._diffsigma(epr.to('GeV').value, eph.to('GeV').value)

    return diffsigma


def generate_lut_pp(Ep=np.logspace(0.085623713910610105,7,800)*u.GeV,
        Eg=np.logspace(-5,3,1024)*u.TeV, out_base='PionDecayKafexhiu14_LUT_',
        hiEmodel=None, nuclear_enhancement=True):
    from emcee.interruptible_pool import InterruptiblePool as Pool

    pool = Pool()
    if hiEmodel is None:
        hiEmodel = ['Geant4','Pythia8','SIBYLL','QGSJET']
    elif type(hiEmodel) is str:
        hiEmodel = [hiEmodel,]

    if nuclear_enhancement:
        out_base += 'NucEnh_'

    for model in hiEmodel:
        out_file = out_base + model + '.npz'
        print('Saving LUT for model {0} in {1}...'.format(model,out_file))
        args = [(Ep, eg, model, nuclear_enhancement) for eg in Eg]
        diffsigma_list = pool.map(_calc_lut_pp,args)

        diffsigma = np.array(diffsigma_list).T

        np.savez_compressed(out_file, X=np.log10(Ep.to('GeV').value),
                Y=np.log10(Eg.to('GeV').value), lut=np.log10(diffsigma))
