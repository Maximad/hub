"""Safety regression tests for the production CA mount preflight."""
import unittest

from check_mikrotik_compose_mounts import ROUTER_SERVICES, config_findings


class EffectiveOverlayTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'services': {
                name: {
                    'environment': {
                        'MIKROTIK_VERIFY_TLS': 'true',
                        'MIKROTIK_CA_FILE': '/run/hub-secrets/mikrotik-ca.crt',
                    },
                    'volumes': [{
                        'type': 'bind', 'source': '/host/router-ca.crt',
                        'target': '/run/hub-secrets/mikrotik-ca.crt', 'read_only': True,
                    }],
                }
                for name in ROUTER_SERVICES
            }
        }

    def audit(self):
        return config_findings(
            self.config, is_file=lambda name: name == '/host/router-ca.crt',
        )

    def test_complete_readonly_ca_overlay_passes(self):
        self.assertEqual(self.audit(), [])

    def test_missing_router_ca_mount_fails(self):
        self.config['services']['internet-worker']['volumes'] = []
        self.assertTrue(self.audit())

    def test_router_tls_bypass_fails(self):
        self.config['services']['web']['environment']['MIKROTIK_VERIFY_TLS'] = 'false'
        self.assertIn('web: RouterOS TLS verification is not enabled', self.audit())

    def test_writeable_ca_mount_fails(self):
        self.config['services']['management-mcp']['volumes'][0]['read_only'] = False
        self.assertTrue(self.audit())

    def test_missing_source_ca_fails(self):
        self.config['services']['web']['volumes'][0]['source'] = '/gone/missing.crt'
        self.assertTrue(self.audit())

    def test_missing_service_fails(self):
        del self.config['services']['internet-worker']
        self.assertTrue(self.audit())


if __name__ == '__main__':
    unittest.main()
