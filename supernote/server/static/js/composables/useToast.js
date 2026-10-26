import { ref } from 'vue';

const toasts = ref([]);
let nextToastId = 1;

export function dismissToast(id) {
    toasts.value = toasts.value.filter(t => t.id !== id);
}

export function showToast(message, type = 'error', duration = 4000) {
    const id = nextToastId++;
    toasts.value.push({
        id,
        message: String(message || ''),
        type
    });
    if (duration > 0) {
        setTimeout(() => {
            dismissToast(id);
        }, duration);
    }
    return id;
}

export function useToast() {
    return {
        toasts,
        showToast,
        dismissToast
    };
}
