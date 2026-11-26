import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {Adapter, messageData, supported} from '../src/whatsapp_terminal/bridge/bridge.mjs';

test('entry point starts through a symlinked path', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'wat-entry-'));
  try {
    const link = path.join(directory, 'helper.mjs');
    fs.symlinkSync(fileURLToPath(new URL('../src/whatsapp_terminal/bridge/bridge.mjs', import.meta.url)), link);
    const invalidData = path.join(directory, 'not-a-directory');
    fs.writeFileSync(invalidData, '');
    const result = spawnSync(process.execPath, [link, invalidData], {encoding: 'utf8', timeout: 5000});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /ENOTDIR/);
  } finally {
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

function fixture() {
  const events = [], calls = [];
  const adapter = new Adapter(event => events.push(event));
  const socket = {
    ev: new EventEmitter(), user: {id: '999@s.whatsapp.net', lid: '999@lid'},
    resyncAppState: async (...args) => calls.push(['resync', ...args]),
    chatModify: async (...args) => calls.push(['modify', ...args]),
    fetchMessageHistory: async (...args) => calls.push(['history', ...args]),
    sendMessage: async (...args) => {
      calls.push(['send', ...args]);
      return {key: {id: 'sent'}, messageTimestamp: 200};
    },
    groupFetchAllParticipating: async () => ({'12@g.us': {id: '12@g.us', subject: 'Friends'}}),
  };
  adapter.bind(socket);
  adapter.open = true;
  return {adapter, socket, events, calls};
}

test('history and replayed messages preserve outgoing status without inventing live receipts', () => {
  const {socket, events} = fixture();
  const message = {key: {remoteJid: '123@lid', id: 'sent', fromMe: true},
    message: {conversation: 'Hello'}, status: 4};
  socket.ev.emit('messaging-history.set', {contacts: [], chats: [], messages: [message]});
  socket.ev.emit('messages.upsert', {type: 'append', messages: [{...message, status: 3}]});
  socket.ev.emit('messages.upsert', {type: 'append', messages: [
    {...message, key: {...message.key, fromMe: false}},
    {...message, key: {...message.key, remoteJid: '123@g.us'}},
    {...message, status: 2, userReceipt: [{userJid: '999@lid', readTimestamp: 500}]},
    {...message, status: 2, userReceipt: [{userJid: '123@lid', readTimestamp: 600}]},
  ]});
  const receipts = events.filter(event => event.event === 'receipt');
  assert.deepEqual(receipts.map(event => event.status), ['read', 'delivered', 'read']);
  assert.ok(receipts.every(event => event.historical));
  assert.equal(receipts[0].timestamp, 0);
  assert.equal(receipts.at(-1).timestamp, 600);
});

test('device-address receipts are normalized to the conversation address', () => {
  const {adapter, events} = fixture();
  adapter.receipt({remoteJid: '123:4@s.whatsapp.net', id: 'sent', fromMe: true}, 'read', 500);
  assert.equal(events[0].chat, '123@s.whatsapp.net');
});

test('receipt resolves stored phone/LID mapping before publishing', async () => {
  const {adapter, socket, events} = fixture();
  socket.signalRepository = {lidMapping: {
    getPNForLID: async lid => lid === '123@lid' ? '441234567890@s.whatsapp.net' : null,
    getLIDForPN: async () => '123@lid',
  }};
  await adapter.receipt({remoteJid: '123@lid', id: 'sent', fromMe: true}, 'read', 500);
  assert.deepEqual(events[0], {event: 'identity', pn: '441234567890@s.whatsapp.net', lid: '123@lid'});
  assert.equal(events[1].event, 'receipt');
  events.length = 0;
  socket.signalRepository.lidMapping.getPNForLID = async () => {throw new Error('offline');};
  await adapter.receipt({remoteJid: '123@lid', id: 'sent', fromMe: true}, 'read', 500);
  assert.equal(events[0].event, 'receipt');
});

test('receipt diagnostics omit raw message IDs and addresses', () => {
  const {adapter, events} = fixture();
  adapter.debugReceipts = true;
  adapter.receiptDebug({remoteJid: '441234567890@s.whatsapp.net', id: 'secret-id', fromMe: true}, 'sent');
  assert.match(events[0].text, /outgoing=true sent/);
  assert.ok(!events[0].text.includes('441234567890'));
  assert.ok(!events[0].text.includes('secret-id'));
});

test('supported addresses and text extraction using Baileys', () => {
  assert.equal(supported('123@lid'), true);
  assert.equal(supported('123-456@g.us'), true);
  assert.equal(supported('status@broadcast'), false);
  const message = {
    key: {id: 'one', remoteJid: '123@lid'}, messageTimestamp: {toString: () => '1234'},
    message: {ephemeralMessage: {message: {imageMessage: {caption: 'Photo', jpegThumbnail: Buffer.alloc(50)}}}},
  };
  assert.deepEqual(messageData(message).message, {imageMessage: {caption: 'Photo'}});
  assert.equal(messageData(message).messageTimestamp, 1234);
  assert.equal(messageData({...message, message: {protocolMessage: {}}}), null);
});

test('contacts, identity, history, groups and cached metadata', async () => {
  const {adapter, socket, events} = fixture();
  socket.ev.emit('messaging-history.set', {
    contacts: [{id: '123@lid', name: 'Alice', phoneNumber: '441234567890@s.whatsapp.net'}],
    chats: [{id: '123@lid', name: 'Alice', conversationTimestamp: 400}], messages: [],
  });
  assert.equal((await adapter.request('contacts', {}))[0].name, 'Alice');
  assert.equal(await adapter.request('mute_settings', {chat: '441234567890@s.whatsapp.net'}), 0);
  assert.equal((await adapter.request('groups', {}))[0].subject, 'Friends');
  socket.ev.emit('lid-mapping.update', {pn: '441234567890@s.whatsapp.net', lid: '123@lid'});
  assert.ok(events.some(event => event.event === 'identity'));
});

test('mute uses indefinite/null, preserves finite timestamps, rejects unknown state', async () => {
  const {adapter, socket, calls} = fixture();
  await assert.rejects(adapter.request('mute_settings', {chat: '123@lid'}), /not synced/);
  socket.ev.emit('chats.upsert', [{id: '123@lid', muteEndTime: 1795593416000}]);
  assert.equal(await adapter.request('mute_settings', {chat: '123@lid'}), 1795593416);
  await adapter.request('mute', {chat: '123@lid', until: -1});
  assert.equal(adapter.muteValue('123@lid'), -1);
  await adapter.request('mute', {chat: '123@lid', until: 0});
  assert.deepEqual(calls.filter(call => call[0] === 'modify'), [
    ['modify', {mute: -1}, '123@lid'], ['modify', {mute: null}, '123@lid'],
  ]);
  await assert.rejects(adapter.request('mute', {chat: '123@g.us', until: -1}), /Invalid/);
});

test('phone-address mute updates replace cached LID mute state', () => {
  const {adapter, socket} = fixture();
  adapter.contacts([{id: '123@lid', phoneNumber: '441234567890@s.whatsapp.net', name: 'Alice'}]);
  adapter.contacts([{id: '123@lid', name: undefined}]);
  assert.equal(adapter.cache.contacts['123@lid'].name, 'Alice');
  adapter.chats([{id: '123@lid', muteEndTime: -1}]);
  socket.ev.emit('chats.update', [{id: '441234567890@s.whatsapp.net', muteEndTime: '1795593416000'}]);
  assert.equal(adapter.muteValue('123@lid'), 1795593416);
});

test('only remote one-to-one outgoing receipts produce status events', () => {
  const {socket, events} = fixture();
  const key = {remoteJid: '123@lid', id: 'sent', fromMe: true};
  socket.ev.emit('messages.update', [{key, update: {status: 3}}, {key, update: {status: 4}}]);
  socket.ev.emit('messages.update', [
    {key: {...key, fromMe: false}, update: {status: 4}},
    {key: {...key, remoteJid: '123@g.us'}, update: {status: 4}},
  ]);
  socket.ev.emit('message-receipt.update', [
    {key, receipt: {userJid: '999@lid', readTimestamp: 400}},
    {key, receipt: {userJid: '123@lid', readTimestamp: 500}},
  ]);
  assert.deepEqual(events.map(event => event.status), ['delivered', 'read', 'read']);
  assert.equal(events.at(-1).timestamp, 500);
});

test('history and live events are distinguished, send and sync arguments', async () => {
  const {adapter, socket, events, calls} = fixture();
  const message = {key: {remoteJid: '123@lid', id: 'one'}, message: {conversation: 'Hi'}};
  socket.ev.emit('messages.upsert', {messages: [message], type: 'notify'});
  socket.ev.emit('messages.upsert', {messages: [message], type: 'append'});
  assert.deepEqual(events.map(event => event.live), [true, false]);
  assert.deepEqual(await adapter.request('send', {chat: '123@lid', body: 'Hello'}), {id: 'sent', timestamp: 200});
  await adapter.request('history', {chat: '123@lid', message_id: 'one', from_me: false, timestamp: 100, count: 50});
  assert.deepEqual(calls.at(-1), ['history', 50, {remoteJid: '123@lid', id: 'one', fromMe: false}, 100]);
  adapter.open = false;
  await assert.rejects(adapter.request('send', {chat: '123@lid', body: 'No'}), /Not connected/);
});
