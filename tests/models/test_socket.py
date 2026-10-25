"""Tests for Socket.IO data models and serialization."""

from supernote.models.socket import (
    DigestMessageTemplate,
    ScheduleMessageTemplate,
    SocketDigestMessageData,
    SocketHandshakeParams,
    SocketIoAction,
    SocketIoDigestDataType,
    SocketIoFileType,
    SocketIoMessageTemplate,
    SocketIoMessageType,
    SocketMessageData,
    SocketTaskMessageData,
)


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
    assert restored == params


def test_socket_io_message_template_serialization() -> None:
    template = SocketIoMessageTemplate(
        message_type=SocketIoAction.ADDFOLDER,
        equipment_no="SN123456",
        file_type=SocketIoFileType.FOLDER,
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
        "fileType": "FOLDE",
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

    deserialized = SocketIoMessageTemplate.from_json(template.to_json())
    assert deserialized == template


def test_socket_message_data_envelope_serialization() -> None:
    template1 = SocketIoMessageTemplate(
        message_type=SocketIoAction.MOVEFILE,
        equipment_no="SN999999",
        file_type=SocketIoFileType.FILE,
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
    assert d == {
        "code": "200",
        "msg": "success",
        "timestamp": 1700000005000,
        "msgType": "FILE-SYN",
        "data": [
            {
                "messageType": "MOVEFILE",
                "equipmentNo": "SN999999",
                "fileType": "FILE",
                "id": 42,
                "newId": None,
                "originalName": None,
                "newName": None,
                "md5": None,
                "size": None,
                "directoryId": None,
                "goDirectoryId": None,
                "originalPath": "/Documents/Note.note",
                "newPath": "/Archive/Note.note",
                "timestamp": None,
            }
        ],
    }

    restored = SocketMessageData.from_dict(d)
    assert restored == envelope


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
    assert d == {
        "code": "200",
        "msg": "Success",
        "timestamp": 1700000005000,
        "msgType": "TASK-SYN",
        "data": [
            {
                "messageType": "QUERY",
                "equipmentNo": "SN123456",
                "taskListId": "501",
                "taskId": "1002",
                "timestamp": 1700000005000,
            }
        ],
    }

    restored = SocketTaskMessageData.from_dict(d)
    assert restored == envelope


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
    assert d == {
        "code": "200",
        "msg": "Success",
        "timestamp": 1700000005000,
        "msgType": "DIGEST-SYN",
        "data": [
            {
                "messageType": "ADD_DIGEST",
                "dataType": "2",
                "equipmentNo": "SN123456",
                "summaryGroupId": None,
                "summaryId": None,
                "id": 302,
                "fileId": 10023,
                "name": "Action Items",
                "userId": None,
                "uniqueIdentifier": "uuid-302",
                "parentUniqueIdentifier": "uuid-300",
                "content": "Review Socket.IO specification",
                "sourcePath": "/Note/MeetingNotes.note",
                "dataSource": None,
                "sourceType": None,
                "isSummaryGroup": "N",
                "description": None,
                "tags": None,
                "md5Hash": "e10adc3949ba59abbe56e057f20f883e",
                "metadata": None,
                "commentStr": None,
                "commentHandwriteName": None,
                "handwriteInnerName": None,
                "handwriteMD5": None,
                "creationTime": None,
                "lastModifiedTime": None,
                "isDeleted": None,
                "createTime": None,
                "updateTime": None,
                "timestamp": 1700000005000,
                "author": None,
            }
        ],
    }

    restored = SocketDigestMessageData.from_dict(d)
    assert restored == envelope
