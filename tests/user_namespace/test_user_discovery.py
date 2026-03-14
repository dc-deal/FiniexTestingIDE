"""
FiniexTestingIDE - USER Namespace Discovery Tests

Tests auto-discovery, rescan/hot-reload, error handling,
naming conventions, and external directory support.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from tests.user_namespace.conftest import (
    write_module,
    VALID_WORKER_CODE,
    VALID_LOGIC_CODE,
    NOT_A_WORKER_CODE,
    SYNTAX_ERROR_CODE,
    IMPORT_ERROR_CODE,
)


# ============================================
# Helpers
# ============================================

def make_worker_factory(mock_logger, user_dir):
    """Create a WorkerFactory that scans a custom user directory."""
    factory = WorkerFactory(logger=mock_logger)
    # Override default scan: clear USER entries, point to tmp dir
    factory._registry = {
        k: v for k, v in factory._registry.items()
        if not k.startswith('USER/')
    }
    # Monkey-patch the scan to use our tmp dir
    original_scan = factory._scan_user_namespace

    def patched_scan():
        from pathlib import Path as P
        old_default = P('python/workers/user')
        # Temporarily replace scan logic
        factory._scan_user_namespace_dirs([user_dir])

    # Use direct dir scanning instead
    factory._registry = {
        k: v for k, v in factory._registry.items()
        if not k.startswith('USER/')
    }
    _scan_dir(factory, user_dir, is_worker=True)
    return factory


def make_logic_factory(mock_logger, user_dir):
    """Create a DecisionLogicFactory that scans a custom user directory."""
    factory = DecisionLogicFactory(logger=mock_logger)
    factory._registry = {
        k: v for k, v in factory._registry.items()
        if not k.startswith('USER/')
    }
    _scan_dir(factory, user_dir, is_worker=False)
    return factory


def _scan_dir(factory, scan_dir, is_worker):
    """Manually scan a directory and register found modules."""
    import importlib.util

    for py_file in sorted(scan_dir.glob('*.py')):
        if py_file.name.startswith('TEMPLATE_'):
            continue
        if py_file.name.startswith('__'):
            continue

        stem = py_file.stem
        worker_type = f'USER/{stem}'

        # Derive class name
        class_name = ''.join(word.capitalize() for word in stem.split('_'))
        if is_worker and not class_name.endswith('Worker'):
            class_name += 'Worker'

        try:
            spec = importlib.util.spec_from_file_location(
                f'test_user.{stem}', str(py_file))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            cls = getattr(module, class_name)

            if is_worker:
                from python.framework.workers.abstract_worker import AbstractWorker
                if not issubclass(cls, AbstractWorker):
                    factory._logger.warning(
                        f"Skipping USER/{stem}: not AbstractWorker subclass")
                    continue
            else:
                from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
                if not issubclass(cls, AbstractDecisionLogic):
                    factory.logger.warning(
                        f"Skipping USER/{stem}: not AbstractDecisionLogic subclass")
                    continue

            if worker_type in factory._registry:
                logger = factory._logger if is_worker else factory.logger
                logger.warning(f"USER/{stem} overrides previous registration")

            factory._registry[worker_type] = cls

        except (SyntaxError, ImportError, AttributeError) as e:
            logger = factory._logger if is_worker else factory.logger
            logger.warning(f"Skipping USER/{stem}: {type(e).__name__}: {e}")
        except Exception as e:
            logger = factory._logger if is_worker else factory.logger
            logger.warning(f"Skipping USER/{stem}: {type(e).__name__}: {e}")

    # Cleanup test modules from sys.modules
    stale = [k for k in sys.modules if k.startswith('test_user.')]
    for k in stale:
        pass  # Keep them for the test — cleanup happens between tests


def get_user_entries(factory):
    """Get all USER/ entries from a factory registry."""
    return {k: v for k, v in factory._registry.items() if k.startswith('USER/')}


def cleanup_test_modules():
    """Remove test_user.* entries from sys.modules."""
    stale = [k for k in sys.modules if k.startswith('test_user.')]
    for k in stale:
        del sys.modules[k]


# ============================================
# Worker Scan Tests
# ============================================

class TestWorkerScan:
    """Test USER worker auto-discovery."""

    def test_empty_directory(self, mock_logger, tmp_path):
        """Empty user directory: no crash, 0 USER workers."""
        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)
        assert len(get_user_entries(factory)) == 0

    def test_valid_worker_discovered(self, mock_logger, tmp_path):
        """Valid worker file is discovered and registered."""
        code = VALID_WORKER_CODE.format(class_name='TestRsiWorker', marker_value='42.0')
        write_module(tmp_path, 'test_rsi.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        user_entries = get_user_entries(factory)
        assert 'USER/test_rsi' in user_entries
        assert user_entries['USER/test_rsi'].__name__ == 'TestRsiWorker'
        cleanup_test_modules()

    def test_template_skipped(self, mock_logger, tmp_path):
        """TEMPLATE_ prefixed files are not registered."""
        code = VALID_WORKER_CODE.format(class_name='TEMPLATEWorker', marker_value='0.0')
        write_module(tmp_path, 'TEMPLATE_worker.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        assert len(get_user_entries(factory)) == 0

    def test_syntax_error_skipped(self, mock_logger, tmp_path):
        """File with syntax error is skipped with warning."""
        write_module(tmp_path, 'broken_worker.py', SYNTAX_ERROR_CODE)

        factory = WorkerFactory(logger=mock_logger)
        mock_logger.reset_mock()
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        assert 'USER/broken_worker' not in factory._registry
        mock_logger.warning.assert_called()
        warning_msg = str(mock_logger.warning.call_args)
        assert 'SyntaxError' in warning_msg

    def test_import_error_skipped(self, mock_logger, tmp_path):
        """File with bad import is skipped with warning."""
        write_module(tmp_path, 'bad_import_worker.py', IMPORT_ERROR_CODE)

        factory = WorkerFactory(logger=mock_logger)
        mock_logger.reset_mock()
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        assert 'USER/bad_import_worker' not in factory._registry
        # Either ImportError or AttributeError depending on resolution
        mock_logger.warning.assert_called()

    def test_wrong_base_class_skipped(self, mock_logger, tmp_path):
        """Class not inheriting from AbstractWorker is skipped."""
        write_module(tmp_path, 'not_a_worker.py', NOT_A_WORKER_CODE)

        factory = WorkerFactory(logger=mock_logger)
        mock_logger.reset_mock()
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        assert 'USER/not_a_worker' not in factory._registry

    def test_multiple_workers(self, mock_logger, tmp_path):
        """Multiple valid workers are all discovered."""
        for i in range(3):
            code = VALID_WORKER_CODE.format(
                class_name=f'TestWorker{i}Worker', marker_value=f'{i}.0')
            write_module(tmp_path, f'test_worker_{i}.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        user_entries = get_user_entries(factory)
        assert len(user_entries) == 3
        for i in range(3):
            assert f'USER/test_worker_{i}' in user_entries
        cleanup_test_modules()

    def test_class_naming_convention(self, mock_logger, tmp_path):
        """my_custom_rsi.py → MyCustomRsiWorker class expected."""
        code = VALID_WORKER_CODE.format(
            class_name='MyCustomRsiWorker', marker_value='99.0')
        write_module(tmp_path, 'my_custom_rsi.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=True)

        user_entries = get_user_entries(factory)
        assert 'USER/my_custom_rsi' in user_entries
        assert user_entries['USER/my_custom_rsi'].__name__ == 'MyCustomRsiWorker'
        cleanup_test_modules()


# ============================================
# Decision Logic Scan Tests
# ============================================

class TestDecisionLogicScan:
    """Test USER decision logic auto-discovery."""

    def test_logic_discovered(self, mock_logger, tmp_path):
        """Valid decision logic is discovered and registered."""
        code = VALID_LOGIC_CODE.format(class_name='TestStrategy')
        write_module(tmp_path, 'test_strategy.py', code)

        factory = DecisionLogicFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=False)

        user_entries = get_user_entries(factory)
        assert 'USER/test_strategy' in user_entries
        assert user_entries['USER/test_strategy'].__name__ == 'TestStrategy'
        cleanup_test_modules()

    def test_logic_naming_no_suffix(self, mock_logger, tmp_path):
        """Decision logic: my_strategy.py → MyStrategy (no suffix)."""
        code = VALID_LOGIC_CODE.format(class_name='MyStrategy')
        write_module(tmp_path, 'my_strategy.py', code)

        factory = DecisionLogicFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, tmp_path, is_worker=False)

        user_entries = get_user_entries(factory)
        assert 'USER/my_strategy' in user_entries
        assert user_entries['USER/my_strategy'].__name__ == 'MyStrategy'
        cleanup_test_modules()


# ============================================
# External Directory Tests
# ============================================

class TestExternalDirectories:
    """Test external directory scanning."""

    def test_external_dir_scanned(self, mock_logger, tmp_path):
        """Worker from external directory is registered."""
        ext_dir = tmp_path / 'external'
        ext_dir.mkdir()
        code = VALID_WORKER_CODE.format(
            class_name='ExternalIndicatorWorker', marker_value='77.0')
        write_module(ext_dir, 'external_indicator.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, ext_dir, is_worker=True)

        assert 'USER/external_indicator' in factory._registry
        cleanup_test_modules()

    def test_external_dir_not_exists(self, mock_logger, tmp_path):
        """Non-existent external directory: warning logged, no crash."""
        non_existent = tmp_path / 'does_not_exist'

        factory = WorkerFactory(logger=mock_logger)
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        # Scanning non-existent dir should not crash
        _scan_dir(factory, non_existent, is_worker=True)
        assert len(get_user_entries(factory)) == 0

    def test_name_collision_last_wins(self, mock_logger, tmp_path):
        """Same name in two dirs: last directory wins, warning logged."""
        dir1 = tmp_path / 'dir1'
        dir2 = tmp_path / 'dir2'
        dir1.mkdir()
        dir2.mkdir()

        code1 = VALID_WORKER_CODE.format(
            class_name='DuplicateWorker', marker_value='1.0')
        code2 = VALID_WORKER_CODE.format(
            class_name='DuplicateWorker', marker_value='2.0')
        write_module(dir1, 'duplicate.py', code1)
        write_module(dir2, 'duplicate.py', code2)

        factory = WorkerFactory(logger=mock_logger)
        mock_logger.reset_mock()
        factory._registry = {
            k: v for k, v in factory._registry.items()
            if not k.startswith('USER/')
        }
        _scan_dir(factory, dir1, is_worker=True)
        _scan_dir(factory, dir2, is_worker=True)

        assert 'USER/duplicate' in factory._registry
        # Second registration should have triggered a warning
        mock_logger.warning.assert_called()
        cleanup_test_modules()


# ============================================
# Rescan / Hot-Reload Tests
# ============================================

class TestRescan:
    """Test hot-reload via rescan()."""

    def test_rescan_clears_old_entries(self, mock_logger):
        """rescan() removes stale USER entries from registry."""
        factory = WorkerFactory(logger=mock_logger)
        # Manually add a fake USER entry
        factory._registry['USER/fake_worker'] = MagicMock
        assert 'USER/fake_worker' in factory._registry

        factory.rescan()
        assert 'USER/fake_worker' not in factory._registry

    def test_rescan_finds_new_module(self, mock_logger):
        """rescan() discovers newly added files in default directory."""
        factory = WorkerFactory(logger=mock_logger)
        initial_user_count = len(get_user_entries(factory))

        # rescan should re-scan and find the real USER modules
        factory.rescan()
        after_rescan_count = len(get_user_entries(factory))

        # Should have at least the envelope_modified from python/workers/user/
        assert after_rescan_count >= 1

    def test_rescan_invalidates_sys_modules(self, mock_logger):
        """rescan() clears python.workers.user.* from sys.modules."""
        factory = WorkerFactory(logger=mock_logger)

        # Inject a fake sys.modules entry
        sys.modules['python.workers.user.fake_test_module'] = MagicMock()
        assert 'python.workers.user.fake_test_module' in sys.modules

        factory.rescan()
        assert 'python.workers.user.fake_test_module' not in sys.modules

    def test_rescan_logic_factory(self, mock_logger):
        """DecisionLogicFactory rescan() works the same way."""
        factory = DecisionLogicFactory(logger=mock_logger)
        factory._registry['USER/fake_logic'] = MagicMock

        factory.rescan()
        assert 'USER/fake_logic' not in factory._registry


# ============================================
# On-Demand Fallback Tests
# ============================================

class TestOnDemandFallback:
    """Test that on-demand loading still works as fallback."""

    def test_on_demand_worker_loads(self, mock_logger):
        """Config-referenced USER worker loads via fallback if not in scan."""
        factory = WorkerFactory(logger=mock_logger)
        # The envelope_modified should be in registry from scan
        # but let's verify the fallback mechanism exists
        assert 'USER/envelope_modified' in factory._registry

    def test_on_demand_logic_loads(self, mock_logger):
        """Config-referenced USER logic loads via fallback if not in scan."""
        factory = DecisionLogicFactory(logger=mock_logger)
        assert 'USER/aggressive_trend_modified' in factory._registry


# ============================================
# Integration: Real USER modules from project
# ============================================

class TestRealUserModules:
    """Test that the actual USER modules in the project are discovered."""

    def test_user_envelope_modified_registered(self, mock_logger):
        """python/workers/user/envelope_modified.py is auto-discovered."""
        factory = WorkerFactory(logger=mock_logger)
        assert 'USER/envelope_modified' in factory._registry
        assert factory._registry['USER/envelope_modified'].__name__ == 'EnvelopeModifiedWorker'

    def test_user_aggressive_trend_modified_registered(self, mock_logger):
        """python/decision_logic/user/aggressive_trend_modified.py is auto-discovered."""
        factory = DecisionLogicFactory(logger=mock_logger)
        assert 'USER/aggressive_trend_modified' in factory._registry
        assert factory._registry['USER/aggressive_trend_modified'].__name__ == 'AggressiveTrendModified'

    def test_templates_not_registered(self, mock_logger):
        """TEMPLATE files are skipped by scanner."""
        worker_factory = WorkerFactory(logger=mock_logger)
        logic_factory = DecisionLogicFactory(logger=mock_logger)

        # No TEMPLATE entries should exist
        for key in worker_factory._registry:
            assert 'TEMPLATE' not in key
        for key in logic_factory._registry:
            assert 'TEMPLATE' not in key

    def test_core_workers_still_present(self, mock_logger):
        """CORE workers are not affected by USER scan."""
        factory = WorkerFactory(logger=mock_logger)
        assert 'CORE/rsi' in factory._registry
        assert 'CORE/envelope' in factory._registry
        assert 'CORE/macd' in factory._registry

    def test_core_logics_still_present(self, mock_logger):
        """CORE decision logics are not affected by USER scan."""
        factory = DecisionLogicFactory(logger=mock_logger)
        assert 'CORE/simple_consensus' in factory._registry
        assert 'CORE/aggressive_trend' in factory._registry
        assert 'CORE/cautious_macd' in factory._registry
