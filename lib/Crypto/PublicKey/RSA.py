# -*- coding: utf-8 -*-
#
#  PublicKey/RSA.py : RSA public key primitive
#
# Written in 2008 by Dwayne C. Litzenberger <dlitz@dlitz.net>
#
# ===================================================================
# The contents of this file are dedicated to the public domain.  To
# the extent that dedication to the public domain is not available,
# everyone is granted a worldwide, perpetual, royalty-free,
# non-exclusive license to exercise all rights associated with the
# contents of this file for any purpose whatsoever.
# No rights are reserved.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ===================================================================

"""RSA public-key cryptography algorithm.

:sort: generate,construct,importKey,error
:undocumented: _fastmath, __revision__, _impl
"""

__revision__ = "$Id$"

__all__ = ['generate', 'construct', 'error', 'importKey' ]

from Crypto.Util.python_compat import *
from Crypto.Util.number import getRandomRange, bytes_to_long, long_to_bytes

from Crypto.PublicKey import _RSA, _slowmath, pubkey
from Crypto import Random

from Crypto.Util.asn1 import DerObject, DerSequence
import binascii
import struct

from Crypto.Util.number import inverse

try:
    from Crypto.PublicKey import _fastmath
except ImportError:
    _fastmath = None

class _RSAobj(pubkey.pubkey):
    """Class defining an actual RSA key."""

    #: Dictionary of RSA parameters.
    #:
    #: A public key will only have the following entries:
    #:
    #:  - **n**, the modulus.
    #:  - **e**, the public exponent.
    #:
    #: A private key will also have:
    #:
    #:  - **d**, the private exponent.
    #:  - **p**, the first factor of n.
    #:  - **q**, the second factor of n.
    #:  - **u**, the CRT coefficient (1/p) mod q.
    keydata = ['n', 'e', 'd', 'p', 'q', 'u']

    def __init__(self, implementation, key, randfunc=None):
        self.implementation = implementation
        self.key = key
        if randfunc is None:
            randfunc = Random.new().read
        self._randfunc = randfunc

    def __getattr__(self, attrname):
        if attrname in self.keydata:
            # For backward compatibility, allow the user to get (not set) the
            # RSA key parameters directly from this object.
            return getattr(self.key, attrname)
        else:
            raise AttributeError("%s object has no %r attribute" % (self.__class__.__name__, attrname,))

    def _encrypt(self, c, K):
        return (self.key._encrypt(c),)

    def _decrypt(self, c):
        #(ciphertext,) = c
        (ciphertext,) = c[:1]  # HACK - We should use the previous line
                               # instead, but this is more compatible and we're
                               # going to replace the Crypto.PublicKey API soon
                               # anyway.

        # Blinded RSA decryption (to prevent timing attacks):
        # Step 1: Generate random secret blinding factor r, such that 0 < r < n-1
        r = getRandomRange(1, self.key.n-1, randfunc=self._randfunc)
        # Step 2: Compute c' = c * r**e mod n
        cp = self.key._blind(ciphertext, r)
        # Step 3: Compute m' = c'**d mod n       (ordinary RSA decryption)
        mp = self.key._decrypt(cp)
        # Step 4: Compute m = m**(r-1) mod n
        return self.key._unblind(mp, r)

    def _blind(self, m, r):
        return self.key._blind(m, r)

    def _unblind(self, m, r):
        return self.key._unblind(m, r)

    def _sign(self, m, K=None):
        return (self.key._sign(m),)

    def _verify(self, m, sig):
        #(s,) = sig
        (s,) = sig[:1]  # HACK - We should use the previous line instead, but
                        # this is more compatible and we're going to replace
                        # the Crypto.PublicKey API soon anyway.
        return self.key._verify(m, s)

    def has_private(self):
        return self.key.has_private()

    def size(self):
        return self.key.size()

    def can_blind(self):
        return True

    def can_encrypt(self):
        return True

    def can_sign(self):
        return True

    def publickey(self):
        return self.implementation.construct((self.key.n, self.key.e))

    def __getstate__(self):
        d = {}
        for k in self.keydata:
            try:
                d[k] = getattr(self.key, k)
            except AttributeError:
                pass
        return d

    def __setstate__(self, d):
        if not hasattr(self, 'implementation'):
            self.implementation = RSAImplementation()
        t = []
        for k in self.keydata:
            if not d.has_key(k):
                break
            t.append(d[k])
        self.key = self.implementation._math.rsa_construct(*tuple(t))

    def __repr__(self):
        attrs = []
        for k in self.keydata:
            if k == 'n':
                attrs.append("n(%d)" % (self.size()+1,))
            elif hasattr(self.key, k):
                attrs.append(k)
        if self.has_private():
            attrs.append("private")
        return "<%s @0x%x %s>" % (self.__class__.__name__, id(self), ",".join(attrs))

    def exportKey(self, format='PEM', passphrase=None):
        """Export this RSA key.

        :Parameter format: The encoding to use to wrap the key.

            - *'DER'* for PKCS#1. Always unencrypted.
            - *'OpenSSH'* for OpenSSH public keys (no private key)
            - *'PEM'* for RFC1421/3. Unencrypted (default) or encrypted.
        :Type format: string

        :Parameter passphrase: In case of PEM, the pass phrase to derive the encryption key from.
        :Type passphrase: string 

        :Return: A string with the encoded public or private half.
        :Raise ValueError:
            When the format is unknown.
        """
        if format=='OpenSSH':
               eb = long_to_bytes(self.e)
               nb = long_to_bytes(self.n)
               if ord(eb[0]) & 0x80: eb='\x00'+nb
               if ord(nb[0]) & 0x80: nb='\x00'+nb
               keyparts = [ 'ssh-rsa', eb, nb ]
               keystring = ''.join([ struct.pack(">I",len(kp))+kp for kp in keyparts]) 
               return 'ssh-rsa '+binascii.b2a_base64(keystring)[:-1]

        # PKCS#1 is a direct DER encoding. PEM uses it as well, but
        # wraps in an ASCII envelope.
        der = DerSequence()
        if self.has_private():
                keyType = "RSA PRIVATE"
                der[:] = [ 0, self.n, self.e, self.d, self.p, self.q,
                           self.d % (self.p-1), self.d % (self.q-1),
                           inverse(self.q, self.p) ]
        else:
                keyType = "PUBLIC"
                der.append('\x30\x0D\x06\x09\x2A\x86\x48\x86\xF7\x0D\x01\x01\x01\x05\x00')
                bitmap = DerObject('BIT STRING')
                derPK = DerSequence()
                derPK[:] = [ self.n, self.e ]
                bitmap.payload = '\x00' + derPK.encode()
                der.append(bitmap.encode())
        if format=='DER':
                return der.encode()
        if format=='PEM':
                pem = "-----BEGIN %s KEY-----\n" % keyType
                keyobj = None
                if passphrase and keyType=='RSA PRIVATE':
                    # We only support 3DES for encryption
                    import Crypto.Hash.MD5
                    from Crypto.Cipher import DES3
                    from Crypto.Protocol.KDF import PBKDF1
                    salt = self._randfunc(8)
                    key =  PBKDF1(passphrase, salt, 16, 1, Crypto.Hash.MD5)
                    key += PBKDF1(key+passphrase, salt, 8, 1, Crypto.Hash.MD5)
                    keyobj = DES3.new(key, Crypto.Cipher.DES3.MODE_CBC, salt)
                    pem += 'Proc-Type: 4,ENCRYPTED\n'
                    pem += 'DEK-Info: DES-EDE3-CBC,' + binascii.b2a_hex(salt).upper() + '\n\n'
                
                binaryKey = der.encode()
                if keyobj:
                    # Add PKCS#7-like padding
                    padding = keyobj.block_size-len(binaryKey)%keyobj.block_size
                    binaryKey = keyobj.encrypt(binaryKey+chr(padding)*padding)

                # Each BASE64 line can take up to 64 characters (=48 bytes of data)
                chunks = [ binascii.b2a_base64(binaryKey[i:i+48]) for i in range(0, len(binaryKey), 48) ]
                pem += ''.join(chunks)
                pem += "-----END %s KEY-----" % keyType
                return pem
        return ValueError("Unknown key format '%s'. Cannot export the RSA key." % format)

class RSAImplementation(object):
    """
    An RSA key factory.

    This class is only internally used to implement the methods of the `Crypto.PublicKey.RSA` modulule.

    :sort: __init__,generate,construct,importKey
    :undocumented: _g*, _i*
    """

    def __init__(self, **kwargs):
        """Create a new RSA key factory.

        :Keywords:
         use_fast_math : bool
                                Specify which mathematic library to use:

                                - *None* (default). Use fastest math available.
                                - *True* . Use fast math.
                                - *False* . Use slow math.
         default_randfunc : callable
                                Specify how to collect random data:

                                - *None* (default). Use Random.new().read().
                                - not *Note* . Use the specified function directly.
        :Raise RuntimeError:
            When **use_fast_math** =True but fast math is not available.
        """
        use_fast_math = kwargs.get('use_fast_math', None)
        if use_fast_math is None:   # Automatic
            if _fastmath is not None:
                self._math = _fastmath
            else:
                self._math = _slowmath

        elif use_fast_math:     # Explicitly select fast math
            if _fastmath is not None:
                self._math = _fastmath
            else:
                raise RuntimeError("fast math module not available")

        else:   # Explicitly select slow math
            self._math = _slowmath

        self.error = self._math.error

        self._default_randfunc = kwargs.get('default_randfunc', None)
        self._current_randfunc = None

    def _get_randfunc(self, randfunc):
        if randfunc is not None:
            return randfunc
        elif self._current_randfunc is None:
            self._current_randfunc = Random.new().read
        return self._current_randfunc

    def generate(self, bits, randfunc=None, progress_func=None, e=65537):
        """Randomly generate a fresh, new RSA key object.

        :Parameters:
         bits : int
                            Key length, or size (in bits) of the RSA modulus.

                            It must be a multiple of 256, and no smaller than 1024.
         randfunc : callable
                            Random number generation function; it should accept
                            a single integer N and return a string of random data
                            N bytes long.
         progress_func : callable
                            Optional function that will be called with a short string
                            containing the key parameter currently being generated;
                            it's useful for interactive applications where a user is
                            waiting for a key to be generated.
         e : int
                            Public RSA exponent. It must be an odd positive integer.

                            It is typically a small number with very few ones in its
                            binary representation.

                            The default value 65537 (= ``0b10000000000000001`` ) is a safe
                            choice: other common values are 5, 7, 17, and 257.

        :attention: You should always use a cryptographically secure random number generator,
            such as the one defined in the ``Crypto.Random`` module; **don't** just use the
            current time and the ``random`` module.

        :attention: Exponent 3 is also widely used, but it requires very special care when padding
            the message.

        :Raise ValueError:
            When **bits** is too little or not a multiple of 256, or when
            **e** is not odd or smaller than 2.
        """
        if bits < 1024 or (bits & 0xff) != 0:
            # pubkey.getStrongPrime doesn't like anything that's not a multiple of 128 and > 512
            raise ValueError("RSA modulus length must be a multiple of 256 and >= 1024")
        if e%2==0 or e<3:
            raise ValueError("RSA public exponent must be a positive, odd integer larger than 2.")
        rf = self._get_randfunc(randfunc)
        obj = _RSA.generate_py(bits, rf, progress_func, e)    # TODO: Don't use legacy _RSA module
        key = self._math.rsa_construct(obj.n, obj.e, obj.d, obj.p, obj.q, obj.u)
        return _RSAobj(self, key)

    def construct(self, tup):
        """Construct an RSA key object from a tuple of valid RSA components.

        The modulus **n** must be the product of two primes.
        The public exponent **e** must be odd and larger than 1.

        In case of a private key, the following equations must apply:

        - e != 1
        - p*q = n
        - e*d = 1 mod (p-1)(q-1)
        - p*u = 1 mod q

        :Parameters:
         tup : tuple
                    A tuple of long integers, with at least 2 and no
                    more than 6 items. The items come in the following order:

                    1. RSA modulus (n).
                    2. Public exponent (e).
                    3. Private exponent (d). Only required if the key is private.
                    4. First factor of n (p). Optional.
                    5. Second factor of n (q). Optional.
                    6. CRT coefficient, (1/p) mod q (u). Optional.
        """
        key = self._math.rsa_construct(*tup)
        return _RSAobj(self, key)

    def _importKeyDER(self, externKey):
        """Import an RSA key (public or private half), encoded in DER form."""
        der = DerSequence()
        der.decode(externKey, True)
        if len(der)==9 and der.hasOnlyInts() and der[0]==0:
                # ASN.1 RSAPrivateKey element
                del der[6:]     # Remove d mod (p-1), d mod (q-1), and q^{-1} mod p
                der.append(inverse(der[4],der[5])) # Add p^{-1} mod q
                del der[0]      # Remove version
                return self.construct(der[:])
        if len(der)==2:
                # The DER object is a SubjectPublicKeyInfo SEQUENCE with two elements:
                # an algorithm SEQUENCE (or algorithmIdentifier) and a subjectPublicKey BIT STRING.
                #
                # The first element is always the same. It contains the oid of
                # the RSA algorithm and its parameters (none).
                # 0x30 0x0D     SEQUENCE, 12 bytes of payload
                #   0x06 0x09   OBJECT IDENTIFIER, 9 bytes of payload
                #     0x2A 0x86 0x48 0x86 0xF7 0x0D 0x01 0x01 0x01
                #               rsaEncryption (1 2 840 113549 1 1 1) (PKCS #1)
                #   0x05 0x00   NULL
                #
                # subjectPublicKey encapsulates the actual ASN.1 RSAPublicKey element.
                if der[0]=='\x30\x0D\x06\x09\x2A\x86\x48\x86\xF7\x0D\x01\x01\x01\x05\x00':
                        bitmap = DerObject()
                        bitmap.decode(der[1], True)
                        if bitmap.typeTag=='\x03' and bitmap.payload[0]=='\x00':
                                der.decode(bitmap.payload[1:], True)
                                if len(der)==2 and der.hasOnlyInts():
                                        return self.construct(der[:])
        raise ValueError("RSA key format is not supported")

    def importKey(self, externKey, passphrase=None):
        """Import an RSA key (public or private half), encoded in standard form.

        :Parameter externKey:
            The RSA key to import, encoded as a string.

            The key can be in DER (PKCS#1), OpenSSH or in PEM format (RFC1421/3).
            In case of PEM, the key can be encrypted with DES or 3TDES according to a certain pass phrase.
            Only OpenSSL-compatible pass phrases are supported.
        :Type externKey: string

        :Parameter passphrase:
            In case of an encrypted PEM key, this is the pass phrase from which the encryption key is derived.
        :Type passphrase: string
        
        :Raise ValueError/IndexError/TypeError:
            When the given key cannot be parsed (possibly because the pass phrase is wrong).
        """
        if externKey.startswith('-----'):
                # This is probably a PEM encoded key
                lines = externKey.replace(" ",'').split()
                keyobj = None

                # The encrypted PEM format
                if lines[1].startswith('Proc-Type:4,ENCRYPTED'):
                    DEK = lines[2].split(':')
                    if len(DEK)!=2 or DEK[0]!='DEK-Info' or not passphrase:
                        raise ValueError("PEM encryption format not supported.")
                    algo, salt = DEK[1].split(',')
                    salt = binascii.a2b_hex(salt)
                    import Crypto.Hash.MD5
                    from Crypto.Cipher import DES, DES3
                    from Crypto.Protocol.KDF import PBKDF1
                    if algo=="DES-CBC":
                        # This is EVP_BytesToKey in OpenSSL
                        key = PBKDF1(passphrase, salt, 8, 1, Crypto.Hash.MD5)
                        keyobj = DES.new(key, Crypto.Cipher.DES.MODE_CBC, salt)
                    elif algo=="DES-EDE3-CBC":
                        # Note that EVP_BytesToKey is note exactly the same as PBKDF1
                        key =  PBKDF1(passphrase, salt, 16, 1, Crypto.Hash.MD5)
                        key += PBKDF1(key+passphrase, salt, 8, 1, Crypto.Hash.MD5)
                        keyobj = DES3.new(key, Crypto.Cipher.DES3.MODE_CBC, salt)
                    else:
                        raise ValueError("Unsupport PEM encryption algorithm.")
                    lines = lines[2:]
                
                der = binascii.a2b_base64(''.join(lines[1:-1]))
                if keyobj:
                    der = keyobj.decrypt(der)
                    padding = ord(der[-1])
                    der = der[:-padding]
                return self._importKeyDER(der)

        if externKey.startswith('ssh-rsa '):
                # This is probably an OpenSSH key
                keystring = binascii.a2b_base64(externKey.split(' ')[1])
                keyparts = []
                while len(keystring)>4:
                    l = struct.unpack(">I",keystring[:4])[0]
                    keyparts.append(keystring[4:4+l])
                    keystring = keystring[4+l:]
                e = bytes_to_long(keyparts[1])
                n = bytes_to_long(keyparts[2])
                return self.construct([n, e])
        if externKey[0]=='\x30':
                # This is probably a DER encoded key
                return self._importKeyDER(externKey)
        raise ValueError("RSA key format is not supported")

_impl = RSAImplementation()
#:
#: Randomly generate a fresh, new RSA key object.
#:
#: See `RSAImplementation.generate`.
#:
generate = _impl.generate
#:
#: Construct an RSA key object from a tuple of valid RSA components.
#:
#: See `RSAImplementation.construct`.
#:
construct = _impl.construct
#:
#: Import an RSA key (public or private half), encoded in standard form.
#:
#: See `RSAImplementation.importKey`.
#:
importKey = _impl.importKey
error = _impl.error

# vim:set ts=4 sw=4 sts=4 expandtab:

