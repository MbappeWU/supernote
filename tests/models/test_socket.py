"""Tests for Socket.IO data models and serialization."""

import json

from supernote.models.socket import (
    DigestMessageTemplate,
    ScheduleMessageTemplate,
    SocketDigestMessageData,
    SocketHandshakeParams,
    SocketIoAction,
    SocketIoCode,
    SocketIoDigestDataType,
    SocketIoFileType,
    SocketIoMessageTemplate,
    SocketIoMessageType,
    SocketMessageData,
    SocketTaskMessageData,
)


def test_socket_io_code_constants() -> None:
    assert SocketIoCode.SUCCESS == 200
    assert SocketIoCode.FORBIDDEN == 403
    assert SocketIoFileType.FILE == "FILE"
    assert SocketIoFileType.FOLDER == "FOLDE"
    assert SocketIoDigestDataType.SUMMARY_GROUP == "1"
    assert SocketIoDigestDataType.SUMMARY == "2"
    assert SocketIoDigestDataType.SUMMARY_TAG == "3"


def test_socket_handshake_params_serialization() -> None:
    params = SocketHandshakeParams(
        token="test-jwt-token",
        type="file",
        random="123456",
        sign="abcdef123456",
    )

    data_dict = params.to_dict()
    assert data_dict == {
        "token": "test-jwt-token",
        "type": "file",
        "random": "123456",
        "sign": "abcdef123456",
    }

    restored = SocketHandshakeParams.from_dict(data_dict)
    assert restored.token == "test-jwt-token"
    assert restored.type == "file"
    assert restored.random == "123456"
    assert restored.sign == "abcdef123456"


def test_socket_io_message_template_serialization() -> None:
    template = SocketIoMessageTemplate(
        message_type=SocketIoAction.ADDFOLDER,
        equipment_no="SN123456",
        file_type="folder",
        id=10,
        original_name="Old Folder",
        new_name="New Folder",
        directory_id=1,
        go_directory_id=2,
        timestamp=1700000000000,
    )

    d = template.to_dict()
    assert d == {
        "messageType": "ADDFOLDER",
        "equipmentNo": "SN123456",
        "fileType": "folder",
        "id": 10,
        "newId": None,
        "originalName": "Old Folder",
        "newName": "New Folder",
        "md5": None,
        "size": None,
        "directoryId": 1,
        "goDirectoryId": 2,
        "originalPath": None,
        "newPath": None,
        "timestamp": 1700000000000,
    }

    # JSON roundtrip
    json_str = json.dumps(d)
    deserialized = SocketIoMessageTemplate.from_dict(json.loads(json_str))
    assert deserialized.message_type == SocketIoAction.ADDFOLDER
    assert deserialized.equipment_no == "SN123456"
    assert deserialized.new_name == "New Folder"


def test_socket_message_data_envelope_serialization() -> None:
    template1 = SocketIoMessageTemplate(
        message_type=SocketIoAction.MOVEFILE,
        equipment_no="SN999999",
        file_type="note",
        id=42,
        original_path="/Documents/Note.note",
        new_path="/Archive/Note.note",
    )

    envelope = SocketMessageData(
        code="200",
        msg="success",
        timestamp=1700000005000,
        msg_type=SocketIoMessageType.FILE_SYN,
        data=[template1],
    )

    d = envelope.to_dict()
    assert d["code"] == "200"
    assert d["msg"] == "success"
    assert d["msgType"] == "FILE-SYN"
    assert len(d["data"]) == 1
    assert d["data"][0]["messageType"] == "MOVEFILE"
    assert d["data"][0]["originalPath"] == "/Documents/Note.note"

    restored = SocketMessageData.from_dict(d)
    assert restored.code == "200"
    assert restored.msg_type == SocketIoMessageType.FILE_SYN
    assert len(restored.data) == 1
    assert restored.data[0].original_path == "/Documents/Note.note"


def test_schedule_message_template_and_task_envelope_serialization() -> None:
    task_item = ScheduleMessageTemplate(
        message_type=SocketIoAction.QUERY,
        equipment_no="SN123456",
        task_list_id="501",
        task_id="1002",
        timestamp=1700000005000,
    )
    envelope = SocketTaskMessageData(
        code="200",
        msg="Success",
        timestamp=1700000005000,
        data=[task_item],
    )

    d = envelope.to_dict()
    assert d["msgType"] == "TASK-SYN"
    assert d["data"] == [
        {
            "messageType": "QUERY",
            "equipmentNo": "SN123456",
            "taskListId": "501",
            "taskId": "1002",
            "timestamp": 1700000005000,
        }
    ]

    restored = SocketTaskMessageData.from_dict(d)
    assert restored.msg_type == SocketIoMessageType.TASK_SYN
    assert len(restored.data) == 1
    assert restored.data[0].task_list_id == "501"
    assert restored.data[0].task_id == "1002"


def test_digest_message_template_and_digest_envelope_serialization() -> None:
    digest_item = DigestMessageTemplate(
        message_type=SocketIoAction.ADD_DIGEST,
        data_type=SocketIoDigestDataType.SUMMARY,
        equipment_no="SN123456",
        id=302,
        file_id=10023,
        name="Action Items",
        unique_identifier="uuid-302",
        parent_unique_identifier="uuid-300",
        content="Review Socket.IO specification",
        source_path="/Note/MeetingNotes.note",
        is_summary_group="N",
        md5_hash="e10adc3949ba59abbe56e057f20f883e",
        timestamp=1700000005000,
    )
    envelope = SocketDigestMessageData(
        code="200",
        msg="Success",
        timestamp=1700000005000,
        data=[digest_item],
    )

    d = envelope.to_dict()
    assert d["msgType"] == "DIGEST-SYN"
    assert len(d["data"]) == 1
    assert d["data"][0]["messageType"] == "ADD_DIGEST"
    assert d["data"][0]["dataType"] == "2"
    assert d["data"][0]["id"] == 302
    assert d["data"][0]["uniqueIdentifier"] == "uuid-302"

    restored = SocketDigestMessageData.from_dict(d)
    assert restored.msg_type == SocketIoMessageType.DIGEST_SYN
    assert len(restored.data) == 1
    assert restored.data[0].data_type == SocketIoDigestDataType.SUMMARY
    assert restored.data[0].content == "Review Socket.IO specification"
