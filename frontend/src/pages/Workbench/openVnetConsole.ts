// "Enter the collection network admin console": shared by the Worker management / external gateway pages.
// First synchronously open an empty window (within the user gesture, so it isn't popup-blocked), then fetch the keyed URL and fill it in; on failure close it and toast.
import { aiTelemetryApi } from '../../services/aiTelemetryApi';
import { toast } from '../../components/ui/toast';

export async function openVnetConsole(token: string): Promise<void> {
  const win = window.open('', '_blank', 'noopener');
  try {
    const { url } = await aiTelemetryApi.adminVnetConsole(token);
    if (win) win.location.href = url; else window.location.href = url;
  } catch (e) {
    win?.close();
    toast.error((e as Error)?.message || '进入采集网络管理台失败');
  }
}
