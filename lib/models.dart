import 'dart:typed_data';

import 'package:flutter/material.dart';

enum FriendState { pendingIn, pendingOut, friend }

enum MsgKind { text, image, voice, file, system, link, video }

enum SendState { sending, sent, failed }

class Channel {
  Channel(this.id, this.name, this.model, this.baseUrl, {this.keyFilled = false, this.shared = true});
  final String id;
  String name;
  String model;
  String baseUrl;
  bool keyFilled;
  bool shared;
}

class MemoryItem {
  MemoryItem(this.id, this.text);
  final String id;
  String text;
}

class Persona {
  Persona({
    required this.id,
    required this.name,
    required this.color,
    required this.bio,
    required this.channelId,
    this.state = FriendState.friend,
    this.isBot = false,
    this.verify = '',
    this.remark = '',
    this.phone = '',
    this.desc = '',
    this.greet = false,
    this.momentsOn = false,
    this.muted = false,
    this.starred = false,
    this.blacklisted = false,
    this.hideHisMoments = false,
    this.hideMyMoments = false,
    this.quietStart = '23:00',
    this.quietEnd = '08:00',
    this.greetCap = 2,
    this.momentsCap = 1,
    this.gapMinutes = 120,
    this.avatarFileId = '',
    this.voiceReplyPref = 'sometimes',
    this.signature = '',
    List<MemoryItem>? memories,
    List<String>? tagNames,
  })  : memories = memories ?? [],
        tagNames = tagNames ?? [];

  final String id;
  String name;
  Color color;
  String bio;
  String channelId;
  String avatarFileId;
  /// 朋友圈个性签名（短句，不是人设提示词）
  String signature;
  FriendState state;
  bool isBot;
  String verify;
  String remark;
  String phone;
  String desc;
  bool greet;
  bool momentsOn;
  bool muted;
  bool starred;
  bool blacklisted;
  bool hideHisMoments;
  bool hideMyMoments;
  String quietStart;
  String quietEnd;
  int greetCap;
  int momentsCap;
  int gapMinutes;
  /// never | sometimes | always — 约束 ⟦语音⟧，不禁点名唱歌
  String voiceReplyPref;
  final List<MemoryItem> memories;
  final List<String> tagNames;

  String get display => remark.trim().isEmpty ? name : remark.trim();
  String get letter => name.isEmpty ? '#' : name;
}

class Group {
  Group(
    this.id,
    this.name,
    this.memberIds, {
    String? ownerId,
    this.announcement = '',
    this.remark = '',
    this.myNick = '',
    this.showNicks = true,
    this.saved = false,
    this.fold = false,
    this.notifyAtMe = true,
    this.notifyAtAll = true,
    this.notifyAnnounce = true,
    List<String>? watchIds,
  })  : ownerId = ownerId ?? (memberIds.isNotEmpty ? memberIds.first : ''),
        watchIds = watchIds ?? [];

  final String id;
  String name;
  final List<String> memberIds;
  String ownerId;
  /// 群公告
  String announcement;
  /// 群备注（仅自己可见）
  String remark;
  /// 我在本群的昵称
  String myNick;
  bool showNicks;
  /// 保存到通讯录
  bool saved;
  /// 折叠该群聊
  bool fold;
  bool notifyAtMe;
  bool notifyAtAll;
  bool notifyAnnounce;
  /// 免打扰时仍通知的关注成员
  final List<String> watchIds;
}

class Conv {
  Conv({
    required this.id,
    required this.preview,
    required this.time,
    this.personaId,
    this.groupId,
    this.pinned = false,
    this.muted = false,
    this.hidden = false,
    this.strongRemind = false,
    this.bgName = '默认',
    this.bgFileId = '',
    this.draft = '',
    this.unread = 0,
  });

  final String id;
  final String? personaId;
  final String? groupId;
  String preview;
  DateTime time;
  bool pinned;
  bool muted;
  bool hidden;
  /// 单聊「强提醒」
  bool strongRemind;
  /// 当前聊天背景名称：默认 / 浅灰 / 墨绿 / 暖黄 / 相册图片
  String bgName;
  /// 服务器上的背景图 file_id（与 bgImageBytes 二选一优先）
  String bgFileId;
  /// 从相册选的自定义背景（本地预览 / 缓存）
  Uint8List? bgImageBytes;
  String draft;
  int unread;

  bool get isGroup => groupId != null;
}

class Msg {
  Msg({
    required this.id,
    required this.convId,
    required this.mine,
    required this.senderId,
    required this.kind,
    required this.text,
    required this.time,
    this.state = SendState.sent,
    this.quoteId,
    this.seconds = 0,
    this.bytes = 0,
    this.heard = false,
    this.backup,
    this.meta,
  });

  final String id;
  final String convId;
  final bool mine;
  final String senderId;
  MsgKind kind;
  String text;
  final DateTime time;
  SendState state;
  String? quoteId;
  int seconds;
  int bytes;
  bool heard;
  String? backup;
  /// 链接卡 / 视频 / 任务进度等结构化字段（JSON map）。
  Map<String, dynamic>? meta;

  bool get revoked => kind == MsgKind.system && backup != null;
}

class MomentComment {
  MomentComment(this.author, this.text, {this.replyTo});
  final String author;
  final String text;
  final String? replyTo;
}

/// 朋友圈可见范围（对应微信「谁可以看」）。
enum MomentVisibility { open, privateOnly, partial, exclude }

class Moment {
  Moment({
    required this.id,
    required this.authorId,
    required this.text,
    required this.time,
    this.images = 0,
    this.private = false,
    this.visibility = MomentVisibility.open,
    this.location,
    this.sample = false,
    List<String>? likes,
    List<MomentComment>? comments,
    List<String>? mentionIds,
    List<String>? audienceIds,
    List<String>? mediaIds,
  })  : likes = likes ?? [],
        comments = comments ?? [],
        mentionIds = mentionIds ?? [],
        audienceIds = audienceIds ?? [],
        mediaIds = mediaIds ?? [];

  final String id;
  final String authorId;
  final String text;
  final DateTime time;
  int images;
  bool private;
  MomentVisibility visibility;
  String? location;
  bool sample;
  final List<String> likes;
  final List<MomentComment> comments;
  final List<String> mentionIds;
  final List<String> audienceIds;
  final List<String> mediaIds;

  String get visibilityLabel => switch (visibility) {
        MomentVisibility.open => '公开',
        MomentVisibility.privateOnly => '私密',
        MomentVisibility.partial => '部分可见',
        MomentVisibility.exclude => '不给谁看',
      };
}

class MomentNotice {
  MomentNotice(this.id, this.momentId, this.who, this.text, this.time, {this.read = false});
  final String id;
  final String momentId;
  final String who;
  final String text;
  final DateTime time;
  bool read;
}

class TagGroup {
  TagGroup(this.name, this.memberIds);
  final String name;
  final List<String> memberIds;
}
