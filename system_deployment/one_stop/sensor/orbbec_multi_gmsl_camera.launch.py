"""Official-style multi-GMSL launch for two Gemini 305g cameras."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.actions import PushRosNamespace


def generate_launch_description():
    package_dir = get_package_share_directory("orbbec_camera")
    gmsl_launch = os.path.join(package_dir, "examples/gmsl_camera", "gemini_330_gmsl.launch.py")
    container = "shared_orbbec_container"
    config = LaunchConfiguration("config_file_path")
    common = {
        "device_num": "2",
        "sync_mode": "standalone",
        "config_file_path": config,
        "attach_to_shared_component_container": "true",
        "component_container_name": container,
    }
    return LaunchDescription([
        DeclareLaunchArgument("left_gmsl_port"),
        DeclareLaunchArgument("right_gmsl_port"),
        DeclareLaunchArgument("config_file_path"),
        Node(
            name=container,
            package="rclcpp_components",
            executable="component_container_mt",
            output="log",
        ),
        # Start gmsl2-7 first, matching the vendor multi_gmsl_camera example.
        TimerAction(period=0.0, actions=[GroupAction([
            PushRosNamespace("/zj_humanoid/sensor"), IncludeLaunchDescription(
                PythonLaunchDescriptionSource(gmsl_launch),
                launch_arguments={**common, "camera_name": "right_wrist", "usb_port": LaunchConfiguration("right_gmsl_port")}.items(),
            )])]),
        TimerAction(period=2.0, actions=[GroupAction([
            PushRosNamespace("/zj_humanoid/sensor"), IncludeLaunchDescription(
                PythonLaunchDescriptionSource(gmsl_launch),
                launch_arguments={**common, "camera_name": "left_wrist", "usb_port": LaunchConfiguration("left_gmsl_port")}.items(),
            )])]),
    ])
