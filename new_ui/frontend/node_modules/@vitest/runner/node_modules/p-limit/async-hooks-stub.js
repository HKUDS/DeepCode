export const AsyncResource = {
	bind(fn, _type, thisArg) {
		return fn.bind(thisArg);
	},
};

export class AsyncLocalStorage {
	getStore() {
		return undefined;
	}

	run(_store, callback) {
		return callback();
	}
}
