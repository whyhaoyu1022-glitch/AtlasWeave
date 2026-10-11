from atlasweave.app.logging_setup import configure_logging


def test_logs_include_runtime_and_keep_at_most_five_files(tmp_path):
    directory = tmp_path / "logs"
    logger = configure_logging(directory)
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 4
    text = (directory / "atlasweave.log").read_text(encoding="utf-8")
    for key in ("AtlasWeave=", "Python=", "PyQt=", "Qt=", "OS="):
        assert key in text
    for index in range(7):
        logger.info("轮换验证 %s", index)
        handler.doRollover()
    assert len(list(directory.glob("atlasweave.log*"))) == 5
    configure_logging(directory)
    assert len(logger.handlers) == 1
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
