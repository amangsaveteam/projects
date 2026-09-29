"""Launch one Orbbec wrist below the product sensor namespace."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import PushRosNamespace
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    vendor_launch = os.path.join(
        get_package_share_directory("orbbec_camera"), "launch", "gemini_301_series.launch.py"
    )
    camera_name = LaunchConfiguration("camera_name")
    usb_port = LaunchConfiguration("usb_port")
    config_file_path = LaunchConfiguration("config_file_path")
    return LaunchDescription([
        DeclareLaunchArgument("camera_name"),
        DeclareLaunchArgument("usb_port"),
        DeclareLaunchArgument(
            "config_file_path",
            default_value="/opt/ros/humble/share/orbbec_camera/config/gemini305_dual_color.yaml",
        ),
        GroupAction([
            PushRosNamespace("/zj_humanoid/sensor"),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(vendor_launch),
                launch_arguments={
                    "camera_name": camera_name,
                    "usb_port": usb_port,
                    "enable_point_cloud": "false",
                    "config_file_path": config_file_path,
                }.items(),
            ),
        ]),
    ])
