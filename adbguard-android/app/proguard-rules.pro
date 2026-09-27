# 无障碍/设备管理组件不能被混淆掉，否则系统找不到
-keep class top.adbguard.GuardAccessibilityService { *; }
-keep class top.adbguard.GuardDeviceAdminReceiver { *; }
-keep class top.adbguard.GuardActionReceiver { *; }
-keep class top.adbguard.GuardService { *; }
