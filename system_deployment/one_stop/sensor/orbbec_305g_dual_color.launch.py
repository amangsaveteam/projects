"""Launch one Gemini 305g using the vendor's official 301-series driver."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    vendor_launch = os.path.join(
        get_package_share_directory("orbbec_camera"),
        "launch",
        "gemini_301_series.launch.py",
    )
    return LaunchDescription([
        DeclareLaunchArgument("camera_name"),
        DeclareLaunchArgument("usb_port"),
        DeclareLaunchArgument(
            "config_file_path",
            default_value=(
                "/opt/ros/humble/share/orbbec_camera/config/"
                "gemini305_dual_color.yaml"
            ),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(vendor_launch),
            launch_arguments={
                "camera_name": LaunchConfiguration("camera_name"),
                "usb_port": LaunchConfiguration("usb_port"),
                "enable_point_cloud": "false",
                "device_preset": "Dual Color Streams",
                "config_file_path": LaunchConfiguration("config_file_path"),
            }.items(),
        ),
    ])
