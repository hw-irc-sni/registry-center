// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Review trail modal — on-demand query of one agent card's position in the
 * review state machine: current status plus the newest-first transition
 * trail (from -> to, reason, operator, timestamp) from the review-status API.
 * Opened from the registry table's action column.
 */

import { useEffect, useState } from 'react'
import {
    Archive,
    CheckCircle2,
    Clock,
    History,
    X,
    XCircle,
} from 'lucide-react'
import { getAgentReviewStatus } from '@/service/api.js'

const STATE_META = {
    published: { icon: CheckCircle2, pill: 'bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-900/30 dark:border-emerald-800 dark:text-emerald-400' },
    registered: { icon: Clock, pill: 'bg-amber-50 border-amber-200 text-amber-700 dark:bg-amber-900/30 dark:border-amber-800 dark:text-amber-400' },
    rejected: { icon: XCircle, pill: 'bg-red-50 border-red-200 text-red-700 dark:bg-red-900/30 dark:border-red-800 dark:text-red-400' },
    deprecated: { icon: Archive, pill: 'bg-zinc-100 border-zinc-300 text-zinc-600 dark:bg-zinc-800/60 dark:border-zinc-700 dark:text-zinc-400' },
}

const TRAIL_DOT = {
    published: 'border-emerald-500 text-emerald-600 dark:text-emerald-400',
    registered: 'border-amber-500 text-amber-600 dark:text-amber-400',
    rejected: 'border-red-500 text-red-600 dark:text-red-400',
    deprecated: 'border-zinc-400 text-zinc-500 dark:text-zinc-400',
}

const STATUS_KEYS = Object.keys(STATE_META)

// Localized status label: never show a raw i18n key when the locale misses
// a status; unknown statuses fall back to their stored value.
function statusLabel(t, status) {
    if (!status) return '—'
    return STATUS_KEYS.includes(status) ? t(`registry.status_${status}`) : status
}

export function StatusPill({ status, t }) {
    const meta = STATE_META[status] || STATE_META.registered
    const Icon = meta.icon
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-black uppercase border ${meta.pill}`}>
            <Icon size={10} />
            {statusLabel(t, status)}
        </span>
    )
}

function TrailBody({ organization, name, api, t }) {
    const [trail, setTrail] = useState(null)
    const [error, setError] = useState(false)

    useEffect(() => {
        let alive = true
        setTrail(null)
        setError(false)
        getAgentReviewStatus(organization, name, api)
            .then((data) => {
                if (alive) setTrail(data)
            })
            .catch(() => {
                if (alive) setError(true)
            })
        return () => {
            alive = false
        }
    }, [organization, name, api])

    if (error) {
        return <p className="py-8 text-center text-xs font-bold text-zinc-400">{t('registry.review_trail_error')}</p>
    }
    if (!trail) {
        return <p className="py-8 text-center text-xs font-bold text-zinc-400">{t('registry.review_trail_loading')}</p>
    }

    const history = trail.history || []
    return (
        <div className="px-6 py-5">
            <div className="flex items-center gap-2 mb-5">
                <span className="text-[10px] font-black uppercase tracking-wider text-zinc-400">
                    {t('registry.review_current')}
                </span>
                <StatusPill status={trail.status} t={t} />
            </div>
            {history.length === 0 ? (
                <p className="py-6 text-center text-xs font-bold text-zinc-400">{t('registry.review_trail_empty')}</p>
            ) : (
                <ol className="relative border-l-2 border-zinc-100 dark:border-zinc-800 ml-2 space-y-4">
                    {history.map((item, index) => {
                        const meta = STATE_META[item.to_status] || STATE_META.registered
                        const Icon = meta.icon
                        const dot = TRAIL_DOT[item.to_status] || TRAIL_DOT.registered
                        return (
                            <li key={index} className="ml-5">
                                <span className={`absolute -left-[9px] flex h-4 w-4 items-center justify-center rounded-full border-2 bg-white dark:bg-zinc-950 ${dot}`}>
                                    <Icon size={9} />
                                </span>
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs font-black text-zinc-900 dark:text-white uppercase">
                                        {statusLabel(t, item.to_status)}
                                    </span>
                                    <span className="text-[10px] font-bold text-zinc-400">
                                        {statusLabel(t, item.from_status)} → {statusLabel(t, item.to_status)}
                                    </span>
                                    <span className="text-[10px] font-bold text-zinc-400">
                                        {String(item.at || '').replace('T', ' ').slice(0, 19)}
                                    </span>
                                </div>
                                {item.reason && (
                                    <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400 font-medium">
                                        {t('registry.review_reason')}: {item.reason}
                                    </p>
                                )}
                                {item.operator && (
                                    <p className="text-[10px] font-bold text-zinc-400">
                                        {t('registry.review_operator')}: {item.operator}
                                    </p>
                                )}
                            </li>
                        )
                    })}
                </ol>
            )}
        </div>
    )
}

export default function ReviewTrailModal({ agent, onClose, t, api }) {
    if (!agent) return null
    return (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-6 bg-black/40 dark:bg-black/60 backdrop-blur-sm" onClick={onClose}>
            <div
                className="bg-white dark:bg-zinc-950 w-full max-w-xl rounded-2xl shadow-2xl border border-zinc-200 dark:border-zinc-800 overflow-hidden flex flex-col max-h-[80vh]"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="px-6 py-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between shrink-0">
                    <div className="flex items-center gap-2.5">
                        <div className="p-2 rounded-lg bg-zinc-900 dark:bg-white text-white dark:text-zinc-900 shadow-sm">
                            <History size={16} />
                        </div>
                        <div>
                            <h3 className="text-base font-black text-zinc-900 dark:text-white leading-none">
                                {t('registry.review_trail_title')}
                            </h3>
                            <p className="text-[10px] font-bold text-zinc-400 mt-1">
                                {agent.id} · {agent.provider?.organization}
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
                        <X size={16} className="text-zinc-400" />
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto custom-scrollbar">
                    <TrailBody organization={agent.provider?.organization} name={agent.id} api={api} t={t} />
                </div>
            </div>
        </div>
    )
}
