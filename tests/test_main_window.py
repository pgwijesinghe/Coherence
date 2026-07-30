from PySide6.QtWidgets import QMessageBox

from coherence.config import ChannelConfig
from coherence.daq import discovery
from coherence.daq.discovery import DeviceSummary
from coherence.ui.main_window import MainWindow, _BACKEND_HARDWARE, _BACKEND_SIMULATED


def test_startup_prefers_real_hardware_when_present(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    devices = discovery.list_devices()
    if devices:
        assert window._backend_combo.currentText() == _BACKEND_HARDWARE
        assert window._config.acquisition.device_name == devices[0].name
        assert window._config.acquisition.ai_channels == tuple(
            n.split("/", 1)[-1] for n in devices[0].ai_channel_names
        )
    else:
        assert window._backend_combo.currentText() == _BACKEND_SIMULATED
        assert window._config.acquisition.simulated is True


def test_device_activated_rebuilds_config_after_confirmation(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    fake_device = DeviceSummary(
        name="DevFake", product_type="Fake Card", is_simulated=True,
        ai_channel_names=["DevFake/ai0", "DevFake/ai1"], ao_channel_names=["DevFake/ao0"],
        ai_max_multi_chan_rate_hz=44_100.0,
    )
    window._on_device_activated(fake_device)

    assert window._config.acquisition.device_name == "DevFake"
    assert window._config.acquisition.ai_channels == ("ai0", "ai1")
    assert len(window._config.channels) == 2
    assert len(window._config.ao_channels) == 1
    assert window._backend_combo.currentText() == _BACKEND_HARDWARE


def test_device_activated_declined_leaves_config_unchanged(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    original_device_name = window._config.acquisition.device_name
    fake_device = DeviceSummary(
        name="DevFake", product_type="Fake Card", is_simulated=True,
        ai_channel_names=["DevFake/ai0"], ao_channel_names=[],
    )
    window._on_device_activated(fake_device)

    assert window._config.acquisition.device_name == original_device_name


def test_device_activated_blocked_while_running(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    idx = window._backend_combo.findText(_BACKEND_SIMULATED)
    window._backend_combo.setCurrentIndex(idx)
    window._on_start()
    assert window._pipeline is not None

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    original_device_name = window._config.acquisition.device_name

    fake_device = DeviceSummary(name="DevFake", product_type="Fake", is_simulated=True,
                                 ai_channel_names=["DevFake/ai0"], ao_channel_names=[])
    window._on_device_activated(fake_device)
    assert window._config.acquisition.device_name == original_device_name

    window._on_stop()


def test_start_blocked_with_no_channels_enabled(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    idx = window._backend_combo.findText(_BACKEND_SIMULATED)
    window._backend_combo.setCurrentIndex(idx)
    for ch in window._config.channels:
        ch.enabled = False

    informed = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: informed.append(True))
    window._on_start()

    assert window._pipeline is None
    assert informed == [True]


def test_coherence_warning_ignores_disabled_channels(qtbot):
    """Regression test (user-reported): sample rate 15360, block 1024, AI0/AO0 at
    15 Hz -- exactly bin 1, perfectly coherent -- but the status bar warned
    'off-bin by 5 Hz' because DISABLED channels still carrying the old 6400 Hz
    placeholder (1/3 bin off at the new rate) were included in the check."""
    window = MainWindow()
    qtbot.addWidget(window)

    window._config.acquisition.sample_rate_hz = 15_360.0
    window._config.acquisition.block_size = 1024
    window._config.acquisition.ai_channels = ("ai0", "ai1", "ai2", "ai3")
    window._config.channels = [
        ChannelConfig(name="AI0", frequency_hz=15.0, input_channel=0, enabled=True),
        ChannelConfig(name="AI1", frequency_hz=6400.0, input_channel=1, enabled=False),
        ChannelConfig(name="AI2", frequency_hz=6400.0, input_channel=2, enabled=False),
        ChannelConfig(name="AI3", frequency_hz=6400.0, input_channel=3, enabled=False),
    ]
    from coherence.config import AOChannelConfig

    window._config.ao_channels = [
        AOChannelConfig(name="AO0", frequency_hz=15.0, ao_channel="ao0", amplitude_v=1.0, enabled=True),
    ]

    window._apply_channels_to_widgets()
    assert "Coherent" in window._coherence_label.text()

    # ...but an off-bin ENABLED channel must still warn, and name itself.
    window._config.channels[1].enabled = True
    window._apply_channels_to_widgets()
    assert "AI1" in window._coherence_label.text()
    assert "5.00" in window._coherence_label.text()


def test_refresh_only_paints_the_visible_tab(qtbot, monkeypatch):
    """Painting every plot widget on every tick regardless of visibility was the
    main GUI-thread load -- and via the GIL, a real cause of acquisition overruns."""
    import numpy as np

    from coherence.dsp.fft_engine import BlockResult, ChannelResult, SpectrumView

    window = MainWindow()
    qtbot.addWidget(window)

    name = window._config.channels[0].name
    window._data_store.ingest(
        BlockResult(
            block_start_sample=0,
            timestamp_s=0.0,
            channels={name: ChannelResult(name=name, frequency_hz=1000.0, amplitude=1.0,
                                          phase_rad=0.0, x=1.0, y=0.0)},
            spectra={0: SpectrumView(input_channel=0, freqs_hz=np.array([0.0, 1.0]),
                                     magnitude_db=np.array([-100.0, -3.0]))},
        )
    )

    calls = {"amp": 0, "spectrum": 0, "polar": 0}
    monkeypatch.setattr(window._amp_phase_view, "update_from_snapshot",
                        lambda *_: calls.__setitem__("amp", calls["amp"] + 1))
    monkeypatch.setattr(window._spectrum_view, "update_from_snapshot",
                        lambda *_: calls.__setitem__("spectrum", calls["spectrum"] + 1))
    monkeypatch.setattr(window._polar_view, "update_from_snapshot",
                        lambda *_: calls.__setitem__("polar", calls["polar"] + 1))

    window._tabs.setCurrentWidget(window._hardware_panel)
    window._refresh_ui()
    assert calls == {"amp": 0, "spectrum": 0, "polar": 0}

    window._tabs.setCurrentWidget(window._amp_phase_view)
    window._refresh_ui()
    assert calls == {"amp": 1, "spectrum": 0, "polar": 0}

    window._tabs.setCurrentWidget(window._spectrum_view)
    window._refresh_ui()
    assert calls == {"amp": 1, "spectrum": 1, "polar": 0}

    window._tabs.setCurrentWidget(window._polar_view)
    window._refresh_ui()
    assert calls == {"amp": 1, "spectrum": 1, "polar": 1}


def test_start_only_acquires_enabled_channel_columns(qtbot):
    """Regression test: acquiring every physical AI channel regardless of which are
    enabled was a real contributor to a DAQmx read-overrun on a 4-channel card."""
    window = MainWindow()
    qtbot.addWidget(window)
    idx = window._backend_combo.findText(_BACKEND_SIMULATED)
    window._backend_combo.setCurrentIndex(idx)

    window._config.acquisition.ai_channels = ("ai0", "ai1", "ai2", "ai3")
    window._config.channels = [
        ChannelConfig(name="AI0", frequency_hz=1000.0, input_channel=0, enabled=True),
        ChannelConfig(name="AI1", frequency_hz=1000.0, input_channel=1, enabled=False),
        ChannelConfig(name="AI2", frequency_hz=1000.0, input_channel=2, enabled=False),
        ChannelConfig(name="AI3", frequency_hz=1000.0, input_channel=3, enabled=False),
    ]

    window._on_start()
    try:
        assert window._pipeline is not None
        assert window._pipeline._config.acquisition.ai_channels == ("ai0",)
        assert len(window._pipeline._config.channels) == 1
    finally:
        window._on_stop()
