import pytest

from scripts.measure_android_install import launch_fields, package_identity


def test_accepts_isolated_identity_and_successful_native_launch():
    assert package_identity(
        "package: name='com.uthelper.benchmark' versionCode='2004001' versionName='2.4.1'"
    ) == ("com.uthelper.benchmark", "2004001")
    assert launch_fields("Status: ok\nLaunchState: COLD\nTotalTime: 230\nWaitTime: 235\n")[
        "TotalTime"
    ] == "230"


def test_refuses_user_app_identity_and_unsuccessful_launch():
    with pytest.raises(ValueError, match="Only com.uthelper.benchmark"):
        package_identity("package: name='com.uthelper.uthelper' versionCode='2004001'")
    with pytest.raises(ValueError, match="successful"):
        launch_fields("Status: timeout\nTotalTime: 0\n")
