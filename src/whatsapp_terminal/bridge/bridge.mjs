import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';
import {createHash} from 'node:crypto';
import makeWASocket, {
  Browsers, DisconnectReason, extractMessageContent, jidNormalizedUser,
  makeCacheableSignalKeyStore, proto, useMultiFileAuthState,
} from 'baileys';
import pino from 'pino';

export const numeric = value => Number(value?.toString() ?? 0);
export const supported = jid => typeof jid === 'string' && /^\d[\d-]*@(lid|s\.whatsapp\.net|g\.us)$/.test(jid);

export function messageData(message) {
  const content = extractMessageContent(message.message);
  if (!content || !message.key?.id || !supported(message.key.remoteJid)) return null;
  if (content.protocolMessage || content.reactionMessage || content.senderKeyDistributionMessage) {
    if (Object.keys(content).every(key =>
      ['protocolMessage', 'reactionMessage', 'senderKeyDistributionMessage', 'messageContextInfo'].includes(key)
    )) return null;
  }
  const body = {};
  if (content.conversation) body.conversation = content.conversation;
  if (content.extendedTextMessage) body.extendedTextMessage = {text: content.extendedTextMessage.text};
  for (const kind of ['imageMessage', 'videoMessage', 'documentMessage']) {
    if (content[kind]) body[kind] = {caption: content[kind].caption};
  }
  if (!Object.keys(body).length) body.unsupported = true;
  return {
    key: message.key, pushName: message.pushName,
    messageTimestamp: numeric(message.messageTimestamp), message: body,
  };
}

export class Adapter {
  constructor(emit, cache = {contacts: {}, chats: {}}) {
    this.emit = emit;
    this.cache = cache;
    this.socket = null;
    this.open = false;
    this.save = () => {};
    this.debugReceipts = process.env.WAT_DEBUG_RECEIPTS === '1';
  }

  receiptDebug(key, detail) {
    if (!this.debugReceipts) return;
    const tag = createHash('sha256').update(key?.id || '').digest('hex').slice(0, 12);
    const address = (key?.remoteJid || '').split('@')[1] || 'missing';
    this.emit({event: 'notice', text: '[receipt-debug] ' + tag + ' address=' + address
      + ' outgoing=' + Boolean(key?.fromMe) + ' ' + detail});
  }

  async resolveIdentity(chat) {
    const mapping = this.socket?.signalRepository?.lidMapping;
    if (!mapping) return;
    try {
      let pn, lid;
      if (chat.endsWith('@lid')) {
        lid = chat;
        pn = await mapping.getPNForLID(lid);
      } else if (chat.endsWith('@s.whatsapp.net')) {
        pn = chat;
        lid = await mapping.getLIDForPN(pn);
      }
      if (supported(pn) && supported(lid) && pn.endsWith('@s.whatsapp.net') && lid.endsWith('@lid')) {
        this.emit({event: 'identity', pn, lid});
      }
    } catch {
      this.receiptDebug({remoteJid: chat}, 'identity lookup failed; preserving receipt');
    }
  }

  contacts(entries) {
    const rows = entries.filter(entry => supported(entry.id));
    for (const entry of rows) {
      const defined = Object.fromEntries(Object.entries(entry).filter(([, value]) => value !== undefined));
      this.cache.contacts[entry.id] = {...this.cache.contacts[entry.id], ...defined};
    }
    this.emit({event: 'contacts', contacts: rows});
    this.save();
  }

  chats(entries, full = false) {
    const rows = entries.filter(entry => supported(entry.id)).map(entry => {
      const row = {id: entry.id};
      for (const field of ['name', 'displayName', 'conversationTimestamp', 'lastMessageRecvTimestamp', 'muteEndTime']) {
        if (entry[field] !== undefined) {
          row[field] = ['name', 'displayName'].includes(field) || entry[field] === null
            ? entry[field] : numeric(entry[field]);
        }
      }
      if (full && !('muteEndTime' in row)) row.muteEndTime = 0;
      return row;
    });
    for (const entry of rows) {
      this.cache.chats[entry.id] = {...this.cache.chats[entry.id], ...entry};
      if ('muteEndTime' in entry) {
        for (const contact of Object.values(this.cache.contacts)) {
          const aliases = [contact.id, contact.lid, contact.phoneNumber].filter(Boolean);
          if (!aliases.includes(entry.id)) continue;
          for (const alias of aliases) {
            this.cache.chats[alias] = {
              ...this.cache.chats[alias], id: alias, muteEndTime: entry.muteEndTime,
            };
          }
        }
      }
    }
    this.emit({event: 'chats', chats: rows});
    this.save();
  }

  receipt(key, status, timestamp, historical = false) {
    const chat = jidNormalizedUser(key?.remoteJid);
    this.receiptDebug(key, 'status=' + status + ' historical=' + historical);
    if (!key?.fromMe || !key.id || !supported(chat) || chat.endsWith('@g.us')) return;
    const publish = () => this.emit({
        event: 'receipt', chat, message_id: key.id, historical,
        status, timestamp: numeric(timestamp) || (historical ? 0 : Date.now() / 1000),
      });
    if (this.socket?.signalRepository?.lidMapping) {
      return this.resolveIdentity(chat).then(publish);
    }
    publish();
  }

  messageStatus(message) {
    const status = numeric(message.status);
    if (status === proto.WebMessageInfo.Status.DELIVERY_ACK) {
      this.receipt(message.key, 'delivered', 0, true);
    } else if ([proto.WebMessageInfo.Status.READ, proto.WebMessageInfo.Status.PLAYED].includes(status)) {
      this.receipt(message.key, 'read', 0, true);
    }
    for (const receipt of message.userReceipt || []) {
      const user = jidNormalizedUser(receipt.userJid);
      const own = [this.socket.user?.id, this.socket.user?.lid].filter(Boolean).map(jidNormalizedUser);
      if (!user || own.includes(user)) continue;
      if (receipt.readTimestamp) this.receipt(message.key, 'read', receipt.readTimestamp, true);
      else if (receipt.receiptTimestamp) this.receipt(message.key, 'delivered', receipt.receiptTimestamp, true);
    }
  }

  bind(socket) {
    this.socket = socket;
    socket.ev.on('contacts.upsert', rows => this.contacts(rows));
    socket.ev.on('contacts.update', rows => this.contacts(rows));
    socket.ev.on('chats.upsert', rows => this.chats(rows, true));
    socket.ev.on('chats.update', rows => this.chats(rows));
    socket.ev.on('groups.update', rows => this.chats(rows.map(row => ({id: row.id, name: row.subject}))));
    socket.ev.on('lid-mapping.update', mapping => {
      if (supported(mapping.pn) && supported(mapping.lid)) {
        this.contacts([{id: mapping.lid, phoneNumber: mapping.pn}]);
        this.emit({event: 'identity', ...mapping});
      }
    });
    socket.ev.on('messaging-history.set', history => {
      this.contacts(history.contacts);
      this.chats(history.chats, true);
      const messages = history.messages.map(messageData).filter(Boolean);
      for (let offset = 0; offset < messages.length; offset += 100) {
        this.emit({event: 'history', messages: messages.slice(offset, offset + 100)});
      }
      for (const message of history.messages) this.messageStatus(message);
    });
    socket.ev.on('messages.upsert', update => {
      const messages = update.messages.map(messageData).filter(Boolean);
      this.emit({event: 'messages', messages, live: update.type === 'notify'});
      for (const message of update.messages) this.messageStatus(message);
    });
    socket.ev.on('messages.update', updates => {
      for (const {key, update} of updates) {
        this.receiptDebug(key, 'messages.update status=' + update.status);
        if (update.status === proto.WebMessageInfo.Status.DELIVERY_ACK) this.receipt(key, 'delivered', update.messageTimestamp);
        if (update.status === proto.WebMessageInfo.Status.READ) this.receipt(key, 'read', update.messageTimestamp);
      }
    });
    socket.ev.on('message-receipt.update', updates => {
      for (const {key, receipt} of updates) {
        const user = jidNormalizedUser(receipt.userJid);
        if ([socket.user?.id, socket.user?.lid].filter(Boolean).map(jidNormalizedUser).includes(user)) continue;
        if (receipt.readTimestamp) this.receipt(key, 'read', receipt.readTimestamp);
        else if (receipt.receiptTimestamp) this.receipt(key, 'delivered', receipt.receiptTimestamp);
      }
    });
  }

  muteValue(chat) {
    let record = this.cache.chats[chat];
    if (!record || !('muteEndTime' in record)) {
      const contact = Object.values(this.cache.contacts).find(entry =>
        [entry.id, entry.lid, entry.phoneNumber].includes(chat));
      for (const alias of [contact?.id, contact?.lid, contact?.phoneNumber]) {
        if (alias && 'muteEndTime' in (this.cache.chats[alias] || {})) {
          record = this.cache.chats[alias];
          break;
        }
      }
    }
    if (!record || !('muteEndTime' in record)) {
      throw new Error('Mute state has not synced for this chat. Wait for phone sync and retry.');
    }
    const value = numeric(record.muteEndTime);
    return value > 100_000_000_000 ? value / 1000 : value;
  }

  async request(method, params) {
    if (!this.open) throw new Error('Not connected yet.');
    const socket = this.socket;
    if (params.chat && !supported(params.chat)) throw new Error('Invalid chat address.');
    switch (method) {
      case 'contacts':
        return Object.values(this.cache.contacts);
      case 'groups': {
        const groups = Object.values(await socket.groupFetchAllParticipating());
        this.chats(groups.map(group => ({id: group.id, name: group.subject})));
        return groups.map(group => ({id: group.id, subject: group.subject}));
      }
      case 'send': {
        const sent = await socket.sendMessage(params.chat, {text: params.body, linkPreview: null});
        this.receiptDebug({...sent.key, remoteJid: params.chat, fromMe: true}, 'sent');
        void this.resolveIdentity(params.chat);
        return {id: sent.key.id, timestamp: numeric(sent.messageTimestamp)};
      }
      case 'history':
        return socket.fetchMessageHistory(
          Math.min(500, Math.max(1, params.count)),
          {remoteJid: params.chat, id: params.message_id, fromMe: params.from_me},
          params.timestamp,
        );
      case 'known_mutes':
        return Object.fromEntries(Object.keys(this.cache.chats)
          .filter(chat => 'muteEndTime' in this.cache.chats[chat])
          .map(chat => [chat, this.muteValue(chat)]));
      case 'mute_settings':
        await socket.resyncAppState(['regular_high'], false);
        return this.muteValue(params.chat);
      case 'mute':
        if (params.chat.endsWith('@g.us') || ![-1, 0].includes(params.until)) throw new Error('Invalid mute request.');
        await socket.chatModify({mute: params.until === -1 ? -1 : null}, params.chat);
        this.chats([{id: params.chat, muteEndTime: params.until}]);
        return null;
      default:
        throw new Error('Unknown method: ' + method);
    }
  }
}

export async function main(dataDir) {
  process.umask(0o077);
  const directory = path.join(dataDir, 'baileys');
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const cachePath = path.join(directory, 'metadata.json');
  const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
  const logger = pino({level: 'warn'}, process.stderr);
  const cache = fs.existsSync(cachePath) ? JSON.parse(fs.readFileSync(cachePath, 'utf8'))
    : {contacts: {}, chats: {}};
  const adapter = new Adapter(emit, cache);
  let saveTimer, reconnectTimer, stopped = false, retries = 0;
  const flush = () => {
    clearTimeout(saveTimer);
    fs.writeFileSync(cachePath + '.tmp', JSON.stringify(cache), {mode: 0o600});
    fs.renameSync(cachePath + '.tmp', cachePath);
  };
  adapter.save = () => { clearTimeout(saveTimer); saveTimer = setTimeout(flush, 500); };
  const {state, saveCreds} = await useMultiFileAuthState(path.join(directory, 'auth'));
  let credentials = Promise.resolve();
  const connect = () => {
    if (stopped) return;
    const socket = makeWASocket({
      auth: {creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger)},
      logger, browser: Browsers.ubuntu('Desktop'), syncFullHistory: true,
      markOnlineOnConnect: false, generateHighQualityLinkPreview: false,
      defaultQueryTimeoutMs: 20000,
    });
    adapter.bind(socket);
    socket.ev.on('creds.update', () => {
      credentials = credentials.then(saveCreds).catch(error => {
        emit({event: 'notice', text: 'Could not save session: ' + error.message});
      });
    });
    socket.ev.on('connection.update', update => {
      if (socket !== adapter.socket || stopped) return;
      if (update.qr) emit({event: 'qr', code: update.qr});
      if (update.connection === 'open') {
        adapter.open = true;
        retries = 0;
        emit({event: 'connection', state: 'open'});
        emit({event: 'contacts', contacts: Object.values(cache.contacts)});
        emit({event: 'chats', chats: Object.values(cache.chats)});
        adapter.request('groups', {}).catch(error => emit({event: 'notice', text: error.message}));
      } else if (update.connection === 'close') {
        adapter.open = false;
        const code = update.lastDisconnect?.error?.output?.statusCode;
        const terminal = [DisconnectReason.loggedOut, DisconnectReason.badSession,
          DisconnectReason.connectionReplaced, DisconnectReason.forbidden, 405].includes(code);
        emit({event: 'connection', state: 'closed', error: terminal
          ? 'WhatsApp rejected the session (' + code + '). Check linked devices; see README troubleshooting.'
          : 'Disconnected. Reconnecting…'});
        if (!terminal) reconnectTimer = setTimeout(connect, Math.min(30000, 1000 * 2 ** retries++));
      }
    });
  };
  const stop = async () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    adapter.open = false;
    await adapter.socket?.end(undefined);
    flush();
    await credentials;
  };
  const lines = readline.createInterface({input: process.stdin});
  lines.on('line', async line => {
    let request;
    try {
      request = JSON.parse(line);
      if (request.method === 'stop') {
        await stop();
        emit({id: request.id, result: null});
        process.exit(0);
      }
      const result = await adapter.request(request.method, request.params || {});
      emit({id: request.id, result: result ?? null});
    } catch (error) {
      emit({id: request?.id, error: error.message});
    }
  });
  lines.on('close', () => { stop().finally(() => process.exit(0)); });
  connect();
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  main(process.argv[2]).catch(error => {
    process.stderr.write(error.stack + '\n');
    process.exit(1);
  });
}
