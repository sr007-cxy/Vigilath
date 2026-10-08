import { useCallback, useEffect, useRef } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { BackendError } from '../../services/apiError';
import { contentApi } from '../../services/contentApi';
import {
  ackCtripVerificationResults,
  ctripVerificationOwnerKey,
  readCtripVerificationResults,
  subscribeCtripVerificationResultsAvailable,
  syncCtripVerificationJobs,
  waitForPlugin,
  type CtripVerificationJob,
} from '../../services/wechatSyncPlugin';
import { toast } from '../ui/toast';

function jobFromTask(
  ownerKey: string,
  task: Awaited<ReturnType<typeof contentApi.listMyPublishTasks>>[number],
): CtripVerificationJob | null {
  const verification = task.channel_meta?.url_verification;
  if (verification?.verification_method !== 'platform_api'
      || verification.verification_executor === 'server_pool'
      || verification.platform_code !== 'ctrip'
      || !verification.external_post_id
      || !verification.next_at) return null;
  return {
    ownerKey,
    taskId: task.id,
    articleId: verification.external_post_id,
    candidateUrl: verification.candidate_url || task.channel_meta?.candidate_url || '',
    nextAt: verification.next_at,
    initialDelaySeconds: verification.initial_delay_seconds || 3600,
    confirmationDelaySeconds: verification.confirmation_delay_seconds || 86400,
    firstPublishedAt: verification.first_published_at,
  };
}

/** Hands Ctrip status obtained offline by the plugin to the logged-in web page for reporting; this component itself doesn't query Ctrip. */
export function CtripVerificationSync() {
  const { token, user } = useAuth();
  const running = useRef(false);
  const rerun = useRef(false);

  const sync = useCallback(async () => {
    if (!token || !user?.id) return;
    if (running.current) {
      rerun.current = true;
      return;
    }
    running.current = true;
    try {
      if (!await waitForPlugin(12_000)) return;
      const ownerKey = ctripVerificationOwnerKey(user.id);
      const observations = await readCtripVerificationResults(ownerKey);
      const acknowledged: string[] = [];
      let finalized = 0;
      let rejected = 0;
      let pendingConfirmation = 0;

      for (const observation of observations) {
        try {
          const task = await contentApi.reportPublishVerification(observation.taskId, {
            platform_code: 'ctrip',
            external_post_id: observation.articleId,
            status: observation.state,
            raw_status: observation.rawStatus,
            audit_fail_reason: observation.auditFailStr,
            publish_time: observation.publishTime,
            observation_id: observation.id,
            observed_at: observation.observedAt,
            error_message: observation.errorMessage,
          }, token);
          acknowledged.push(observation.id);
          if (task.status === 'published') finalized += 1;
          else if (task.status === 'failed') rejected += 1;
          else if (task.channel_meta?.url_verification?.status === 'published_pending_confirmation') {
            pendingConfirmation += 1;
          }
        } catch (error) {
          // Local results of another Vigilath account must not be deleted; the original account can still finish reporting next time it logs in.
          if (!(error instanceof BackendError && [401, 403, 404].includes(error.status))) {
            toast.error(`携程审核结果同步失败：${error instanceof Error ? error.message : String(error)}`);
          }
        }
      }
      if (acknowledged.length > 0) {
        await ackCtripVerificationResults(ownerKey, acknowledged);
        window.dispatchEvent(new Event('vigilath:publish-verification-updated'));
        if (rejected > 0) toast.error(`${rejected} 个携程发布任务审核未通过`);
        else if (finalized > 0) toast.success(`${finalized} 个携程发布任务已最终确认`);
        else if (pendingConfirmation > 0) toast.info('携程文章已上线，正在等待稳定确认');
      }

      const tasks = await contentApi.listMyPublishTasks(token, { status: 'url_submitted', limit: 200 });
      const jobs = tasks.map(task => jobFromTask(ownerKey, task)).filter(
        (job): job is CtripVerificationJob => job !== null,
      );
      if (jobs.length > 0) await syncCtripVerificationJobs(jobs);
    } finally {
      running.current = false;
      if (rerun.current) {
        rerun.current = false;
        void sync();
      }
    }
  }, [token, user?.id]);

  useEffect(() => {
    void sync();
    const unsubscribePlugin = subscribeCtripVerificationResultsAvailable(() => { void sync(); });
    const onRequested = () => { void sync(); };
    window.addEventListener('vigilath:ctrip-verification-sync-requested', onRequested);
    return () => {
      unsubscribePlugin();
      window.removeEventListener('vigilath:ctrip-verification-sync-requested', onRequested);
    };
  }, [sync]);

  return null;
}
