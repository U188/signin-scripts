/*
 *
 *
脚本功能：filmix-ai影视库重命名整理刮削
软件版本：2.6
下载地址：
脚本作者：@liul0ng
更新时间：2025
电报频道：https://t.me/GieGie777
问题反馈：@liul0ng
使用声明：此脚本仅供学习与交流，请在下载使用24小时内删除！请勿在中国大陆转载与贩卖！
*******************************
[rewrite_local]
# > filmix-ai影视库重命名整理刮削
^https:\/\/appv3\.filmix\.com\.cn\/api\/v1\/autotask\/hosting-config\/2646\/ url script-response-body https://raw.githubusercontent.com/U188/signin-scripts/refs/heads/master/fm2.js

[mitm]
hostname = appv3.filmix.com.cn
*
*
*/










let obj = JSON.parse($response.body);

obj.extra_data.current_vip_level= 5;


$done({body: JSON.stringify(obj)});