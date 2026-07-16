export interface Env {
	SALES_BOT_TOKEN: string;
	SALES_CHAT_ID: string;
	POWERDOC_WEBHOOK_TOKEN: string;
}

interface PowerDocCallback {
	clientName?: string;
	email?: string;
	phone?: string;
	event?: string;
	signedFormId?: string;
}

function isAuthorized(req: Request, env: Env): boolean {
	const encoder = new TextEncoder();
	const provided = encoder.encode(req.headers.get('token') ?? '');
	const expected = encoder.encode(env.POWERDOC_WEBHOOK_TOKEN);

	const lengthsMatch = provided.byteLength === expected.byteLength;
	return lengthsMatch
		? crypto.subtle.timingSafeEqual(provided, expected)
		: !crypto.subtle.timingSafeEqual(provided, provided);
}

async function sendTelegramMessage(env: Env, text: string): Promise<void> {
	const resp = await fetch(`https://api.telegram.org/bot${env.SALES_BOT_TOKEN}/sendMessage`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ chat_id: env.SALES_CHAT_ID, text, parse_mode: 'Markdown' }),
	});
	if (!resp.ok) {
		throw new Error(`Telegram send failed: ${resp.status} ${await resp.text()}`);
	}
}

export default {
	async fetch(req: Request, env: Env): Promise<Response> {
		if (req.method !== 'POST') {
			return new Response('ok', { status: 200 });
		}

		if (!isAuthorized(req, env)) {
			return new Response('Unauthorized', { status: 401 });
		}

		let payload: PowerDocCallback;
		try {
			payload = await req.json();
		} catch {
			return new Response('Bad Request', { status: 400 });
		}

		const name = payload.clientName?.trim();
		if (!name) {
			console.log('Signed callback with no clientName', JSON.stringify(payload));
			return new Response('ok', { status: 200 });
		}

		try {
			await sendTelegramMessage(env, `הלווווו\n*${name}* חתם על טופס ההרשמה\nאפשר להתקדם ✅`);
		} catch (err) {
			console.error('Failed to send Telegram alert', err);
			return new Response('Internal Error', { status: 500 });
		}

		return new Response('ok', { status: 200 });
	},
};
