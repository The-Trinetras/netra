import inspect

from netra_api.config import Settings
from netra_worker import main


def test_main_import_does_not_start_worker():
    assert inspect.iscoroutinefunction(main.run)
    assert inspect.isfunction(main.main)


def test_build_pools_registers_expected_types_and_configured_concurrency():
    settings = Settings(parse_workers=4, block_workers=5, embed_workers=2,
                        projection_workers=6, activation_workers=1,
                        worker_poll_interval_seconds=0.25)
    pools = main.build_pools(settings, object())
    assert [pool.config.job_types for pool in pools] == [
        ("parse_document",), ("build_blocks",), ("embed_text",),
        ("search_projection",), ("activate_version",),
    ]
    assert [pool.config.concurrency for pool in pools] == [4, 5, 2, 6, 1]
    assert len({pool.config.worker_id for pool in pools}) == 5
    assert all(pool.config.poll_interval_seconds == 0.25 for pool in pools)
