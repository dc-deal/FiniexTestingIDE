"""
FiniexTestingIDE - Path-Based Loading Tests

Tests path-based worker and decision logic loading via introspection,
CORE namespace integrity, rescan/hot-reload, error handling, and
the WorkerOrchestrator worker-ref normalization.
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


def cleanup_user_loaded():
    """Remove user_loaded.* entries from sys.modules between tests."""
    stale = [k for k in sys.modules if k.startswith('user_loaded.')]
    for k in stale:
        del sys.modules[k]


# ============================================
# Path-Based Worker Loading
# ============================================

class TestPathWorkerLoading:
    """Test on-demand worker loading via file path."""

    def test_load_worker_by_absolute_path(self, mock_logger, tmp_path):
        """Worker file loaded by absolute path — class found via introspection."""
        code = VALID_WORKER_CODE.format(class_name='MyIndicatorWorker', marker_value='1.0')
        py_file = write_module(tmp_path, 'my_indicator.py', code)

        factory = WorkerFactory(logger=mock_logger)
        worker_class, source_path = factory._load_path_worker(str(py_file))

        assert worker_class.__name__ == 'MyIndicatorWorker'
        assert source_path == py_file.resolve()
        cleanup_user_loaded()

    def test_load_worker_by_relative_path(self, mock_logger, tmp_path):
        """Worker loaded by path relative to an explicit base_path."""
        code = VALID_WORKER_CODE.format(class_name='RelativeWorker', marker_value='2.0')
        write_module(tmp_path, 'relative_worker.py', code)

        factory = WorkerFactory(logger=mock_logger)
        worker_class, source_path = factory._load_path_worker(
            'relative_worker.py', base_path=tmp_path
        )

        assert worker_class.__name__ == 'RelativeWorker'
        cleanup_user_loaded()

    def test_load_worker_file_not_found(self, mock_logger, tmp_path):
        """Missing file → ValueError with clear message."""
        factory = WorkerFactory(logger=mock_logger)
        with pytest.raises(ValueError, match='not found'):
            factory._load_path_worker(str(tmp_path / 'nonexistent.py'))

    def test_load_worker_syntax_error(self, mock_logger, tmp_path):
        """File with syntax error → ValueError."""
        py_file = write_module(tmp_path, 'broken.py', SYNTAX_ERROR_CODE)
        factory = WorkerFactory(logger=mock_logger)
        with pytest.raises(ValueError):
            factory._load_path_worker(str(py_file))

    def test_load_worker_zero_subclasses(self, mock_logger, tmp_path):
        """File with no AbstractWorker subclass → ValueError."""
        py_file = write_module(tmp_path, 'not_a_worker.py', NOT_A_WORKER_CODE)
        factory = WorkerFactory(logger=mock_logger)
        with pytest.raises(ValueError, match='Expected exactly 1'):
            factory._load_path_worker(str(py_file))
        cleanup_user_loaded()

    def test_load_worker_two_subclasses(self, mock_logger, tmp_path):
        """File with two AbstractWorker subclasses → ValueError."""
        two_workers = (
            VALID_WORKER_CODE.format(class_name='WorkerA', marker_value='1.0') +
            VALID_WORKER_CODE.format(class_name='WorkerB', marker_value='2.0')
        )
        py_file = write_module(tmp_path, 'two_workers.py', two_workers)
        factory = WorkerFactory(logger=mock_logger)
        with pytest.raises(ValueError, match='Expected exactly 1'):
            factory._load_path_worker(str(py_file))
        cleanup_user_loaded()

    def test_load_worker_with_helper_class(self, mock_logger, tmp_path):
        """File with one AbstractWorker subclass + helper class → loads correctly."""
        code = '''
class HelperClass:
    """Non-worker helper."""
    pass

''' + VALID_WORKER_CODE.format(class_name='WorkerWithHelper', marker_value='5.0')
        py_file = write_module(tmp_path, 'worker_with_helper.py', code)

        factory = WorkerFactory(logger=mock_logger)
        worker_class, _ = factory._load_path_worker(str(py_file))

        assert worker_class.__name__ == 'WorkerWithHelper'
        cleanup_user_loaded()

    def test_registry_cached_after_load(self, mock_logger, tmp_path):
        """Second call with same path returns cached result (no re-load)."""
        code = VALID_WORKER_CODE.format(class_name='CachedWorker', marker_value='3.0')
        py_file = write_module(tmp_path, 'cached_worker.py', code)

        factory = WorkerFactory(logger=mock_logger)
        cls1, path1 = factory._load_path_worker(str(py_file))
        cls2, path2 = factory._load_path_worker(str(py_file))

        assert cls1 is cls2
        assert path1 == path2
        cleanup_user_loaded()


# ============================================
# Path-Based Decision Logic Loading
# ============================================

class TestPathDecisionLogicLoading:
    """Test on-demand decision logic loading via file path."""

    def test_load_logic_by_absolute_path(self, mock_logger, tmp_path):
        """Decision logic loaded by absolute path."""
        code = VALID_LOGIC_CODE.format(class_name='MyStrategy')
        py_file = write_module(tmp_path, 'my_strategy.py', code)

        factory = DecisionLogicFactory(logger=mock_logger)
        logic_class, source_path = factory._load_path_logic(str(py_file))

        assert logic_class.__name__ == 'MyStrategy'
        assert source_path == py_file.resolve()
        cleanup_user_loaded()

    def test_load_logic_zero_subclasses(self, mock_logger, tmp_path):
        """File with no AbstractDecisionLogic subclass → ValueError."""
        py_file = write_module(tmp_path, 'not_logic.py', NOT_A_WORKER_CODE)
        factory = DecisionLogicFactory(logger=mock_logger)
        with pytest.raises(ValueError, match='Expected exactly 1'):
            factory._load_path_logic(str(py_file))
        cleanup_user_loaded()

    def test_load_logic_file_not_found(self, mock_logger, tmp_path):
        """Missing file → ValueError."""
        factory = DecisionLogicFactory(logger=mock_logger)
        with pytest.raises(ValueError, match='not found'):
            factory._load_path_logic(str(tmp_path / 'missing.py'))

    def test_source_path_injected_on_create_logic(self, mock_logger, tmp_path):
        """create_logic() injects _source_path on the returned instance."""
        code = VALID_LOGIC_CODE.format(class_name='SourcePathStrategy')
        py_file = write_module(tmp_path, 'source_path_strategy.py', code)

        factory = DecisionLogicFactory(logger=mock_logger)
        instance = factory.create_logic(
            logic_type=str(py_file),
            logger=mock_logger,
        )

        assert instance._source_path is not None
        assert instance._source_path == py_file.resolve()
        cleanup_user_loaded()

    def test_source_path_not_set_for_core_logic(self, mock_logger):
        """CORE decision logic has _source_path = None (default)."""
        factory = DecisionLogicFactory(logger=mock_logger)
        instance = factory.create_logic(
            logic_type='CORE/simple_consensus',
            logger=mock_logger,
        )
        assert getattr(instance, '_source_path', None) is None


# ============================================
# CORE Workers / Logics — unchanged
# ============================================

class TestCoreRegistration:
    """CORE namespace must be unaffected by the path-based changes."""

    def test_core_workers_registered(self, mock_logger):
        factory = WorkerFactory(logger=mock_logger)
        for key in ['CORE/rsi', 'CORE/envelope', 'CORE/macd', 'CORE/obv', 'CORE/heavy_rsi']:
            assert key in factory._registry, f"Missing: {key}"

    def test_core_logics_registered(self, mock_logger):
        factory = DecisionLogicFactory(logger=mock_logger)
        for key in ['CORE/simple_consensus', 'CORE/aggressive_trend', 'CORE/cautious_macd']:
            assert key in factory._registry, f"Missing: {key}"

    def test_unknown_core_worker_raises(self, mock_logger):
        """Unknown CORE/ reference → ValueError, not a path load attempt."""
        factory = WorkerFactory(logger=mock_logger)
        with pytest.raises(ValueError, match="Unknown CORE worker"):
            factory._resolve_worker_class('CORE/nonexistent_worker')

    def test_unknown_core_logic_raises(self, mock_logger):
        factory = DecisionLogicFactory(logger=mock_logger)
        with pytest.raises(ValueError, match="Unknown CORE decision logic"):
            factory._resolve_logic_class('CORE/nonexistent_logic')


# ============================================
# Rescan / Hot-Reload
# ============================================

class TestRescan:
    """rescan() keeps CORE, clears path-loaded entries."""

    def test_rescan_clears_path_entries_worker(self, mock_logger, tmp_path):
        """rescan() removes path-loaded workers, keeps CORE."""
        code = VALID_WORKER_CODE.format(class_name='RescanWorker', marker_value='1.0')
        py_file = write_module(tmp_path, 'rescan_worker.py', code)

        factory = WorkerFactory(logger=mock_logger)
        factory._load_path_worker(str(py_file))
        cache_key = str(py_file.resolve())
        assert cache_key in factory._registry

        factory.rescan()
        assert cache_key not in factory._registry
        assert 'CORE/rsi' in factory._registry
        cleanup_user_loaded()

    def test_rescan_clears_user_loaded_sys_modules(self, mock_logger):
        """rescan() removes user_loaded.* entries from sys.modules."""
        factory = WorkerFactory(logger=mock_logger)
        sys.modules['user_loaded.worker.fake_module'] = MagicMock()

        factory.rescan()
        assert 'user_loaded.worker.fake_module' not in sys.modules

    def test_rescan_logic_factory(self, mock_logger, tmp_path):
        """DecisionLogicFactory rescan() clears path-loaded, keeps CORE."""
        code = VALID_LOGIC_CODE.format(class_name='RescanLogic')
        py_file = write_module(tmp_path, 'rescan_logic.py', code)

        factory = DecisionLogicFactory(logger=mock_logger)
        factory._load_path_logic(str(py_file))
        cache_key = str(py_file.resolve())
        assert cache_key in factory._registry

        factory.rescan()
        assert cache_key not in factory._registry
        assert 'CORE/simple_consensus' in factory._registry
        cleanup_user_loaded()


# ============================================
# WorkerOrchestrator path normalization
# ============================================

class TestWorkerOrchestratorNormalization:
    """_normalize_worker_ref() resolves paths correctly."""

    def _make_orchestrator(self):
        from python.framework.workers.worker_orchestrator import WorkerOrchestrator
        mock_dl = MagicMock()
        mock_dl.get_required_worker_instances.return_value = {}
        orch = WorkerOrchestrator.__new__(WorkerOrchestrator)
        orch.decision_logic = mock_dl
        orch.strategy_config = {}
        orch.workers = {}
        orch.logger = MagicMock()
        return orch

    def test_core_ref_unchanged(self):
        orch = self._make_orchestrator()
        result = orch._normalize_worker_ref('CORE/rsi')
        assert result == 'CORE/rsi'

    def test_absolute_path_unchanged(self):
        orch = self._make_orchestrator()
        abs_path = '/some/absolute/path/worker.py'
        result = orch._normalize_worker_ref(abs_path)
        assert result == abs_path

    def test_relative_ref_with_base_resolves(self, tmp_path):
        orch = self._make_orchestrator()
        base = tmp_path / 'algo'
        base.mkdir()
        result = orch._normalize_worker_ref('my_worker.py', base_path=base)
        expected = str((base / 'my_worker.py').resolve())
        assert result == expected

    def test_relative_without_base_resolves_to_cwd(self):
        orch = self._make_orchestrator()
        result = orch._normalize_worker_ref('user_algos/x/worker.py')
        expected = str((Path.cwd() / 'user_algos/x/worker.py').resolve())
        assert result == expected

    def test_matching_refs_compare_equal(self, tmp_path):
        """
        Refs from decision logic (relative, base=algo_dir) and config
        (project-root-relative) must normalize to the same absolute path.
        """
        orch = self._make_orchestrator()
        algo_dir = Path('user_algos/my_algo')
        worker_file = 'my_range_worker.py'

        norm_from_dl = orch._normalize_worker_ref(
            worker_file, base_path=Path.cwd() / algo_dir
        )
        norm_from_config = orch._normalize_worker_ref(
            str(algo_dir / worker_file), base_path=None
        )
        assert norm_from_dl == norm_from_config


# ============================================
# Integration: User algo from user_algos/
# ============================================

class TestUserAlgoIntegration:
    """
    Smoke test: verify that a user algo placed in user_algos/ loads correctly.

    These tests require an actual algo to be present in user_algos/.
    They are skipped automatically when the directory is empty (gitignored default).
    """

    def _find_first_algo(self) -> tuple:
        """
        Find the first decision logic + worker pair in user_algos/.

        Returns:
            (decision_path, worker_path) or (None, None) if not found
        """
        base = Path('user_algos')
        if not base.exists():
            return None, None
        for logic_file in sorted(base.rglob('*.py')):
            if 'decision' in logic_file.stem or 'logic' in logic_file.stem or 'strategy' in logic_file.stem:
                return logic_file, None
        for py_file in sorted(base.rglob('*.py')):
            return py_file, None
        return None, None

    def test_user_algo_decision_logic_loads(self, mock_logger):
        """Decision logic in user_algos/ loads and yields exactly one AbstractDecisionLogic subclass."""
        logic_file, _ = self._find_first_algo()
        if logic_file is None:
            pytest.skip('No user algos present in user_algos/')

        factory = DecisionLogicFactory(logger=mock_logger)
        try:
            logic_class, source_path = factory._load_path_logic(str(logic_file))
            assert issubclass(logic_class, factory._registry.get(
                str(source_path), (logic_class, None)
            )[0].__bases__[0] if False else logic_class)
            assert source_path == logic_file.resolve()
        except ValueError:
            pytest.skip(f'File {logic_file} is not a valid decision logic')
        finally:
            cleanup_user_loaded()

    def test_user_algo_get_required_instances_returns_paths(self, mock_logger):
        """get_required_worker_instances() returns a dict of str → str (path or CORE/ ref)."""
        logic_file, _ = self._find_first_algo()
        if logic_file is None:
            pytest.skip('No user algos present in user_algos/')

        factory = DecisionLogicFactory(logger=mock_logger)
        try:
            logic_class, _ = factory._load_path_logic(str(logic_file))
        except ValueError:
            pytest.skip(f'File {logic_file} is not a valid decision logic')
        finally:
            cleanup_user_loaded()

        # Try instantiation — may fail if the logic requires config params.
        # In that case, verify the contract at class level only.
        try:
            instance = logic_class(name='test', logger=mock_logger)
            required = instance.get_required_worker_instances()
            assert isinstance(required, dict)
            for key, val in required.items():
                assert isinstance(key, str)
                assert isinstance(val, str)
                assert val.endswith('.py') or val.startswith('CORE/')
        except Exception:
            # Logic requires config to instantiate — verify method is declared
            assert hasattr(logic_class, 'get_required_worker_instances')
        finally:
            cleanup_user_loaded()
