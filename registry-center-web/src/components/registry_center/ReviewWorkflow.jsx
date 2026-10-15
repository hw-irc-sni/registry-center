// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Review workflow view — the admin-facing view of the AgentCard review
 * state machine: per-state counts, the review queue, and the per-card
 * transition trail (review-status API: from -> to, reason, operator, time).
 * Read-only: actions (publish/reject/deprecate) stay in the registry view.
 */

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
    Archive,
    CheckCircle2,
    ChevronDown,
    Clock,
    History,
    ShieldCheck,
    XCircle,
} from 'lucide-react'
import { getAgentReviewStatus } from '@/service/api.js'

const STATE_META = {
    published: { icon: CheckCircle2, pill: 'bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-900/30 dark:border-emerald-800 dark:text-emerald-400' },
    registered: { icon: Clock, pill: 'bg-amber-50 border-amber-200 text-amber-700 dark:bg-amber-900/30 dark:border-amber-800 dark:text-amber-400' },
    rejected: { icon: XCircle, pill: 'bg-red-50 border-red-200 text-red-700 dark:bg-red-900/30 dark:border-red-800 dark:text-red-400' },
    deprecated: { icon: Archive, pill: 'bg-zinc-100 border-zinc-300 text-zinc-600 dark:bg-zinc-800/60 dark:border-zinc-700 dark:text-zinc-400' },
}

function StatusPill({ status, t }) {
    const meta = STATE_META[status] || STATE_META.registered
    const Icon = meta.icon
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-black uppercase border ${meta.pill}`}>
            <Icon size={10} />
            {t(`registry.status_${status}`)}
        </span>
    )
}

const STATE_ORDER = ['registered', 'rejected', 'deprecated', 'published']

const TRAIL_DOT = {
    published: 'border-emerald-500 text-emerald-600 dark:text-emerald-400',
    registered: 'border-amber-500 text-amber-600 dark:text-amber-400',
    rejected: 'border-red-500 text-red-600 dark:text-red-400',
    deprecated: 'border-zinc-400 text-zinc-500 dark:text-zinc-400',
}

function ReviewTrail({ organization, name, api, t }) {
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
        return <p className="px-6 py-4 text-xs font-bold text-zinc-400">{t('registry.review_trail_error')}</p>
    }
    if (!trail) {
        return <p className="px-6 py-4 text-xs font-bold text-zinc-400">{t('registry.review_trail_loading')}</p>
    }

    const history = trail.history || []
    return (
        <div className="px-6 py-4">
            <div className="flex items-center gap-2 mb-4">
                <span className="text-[10px] font-black uppercase tracking-wider text-zinc-400">
                    {t('registry.review_current')}
                </span>
                <StatusPill status={trail.status} t={t} />
            </div>
            {history.length === 0 ? (
                <p className="text-xs font-bold text-zinc-400">{t('registry.review_trail_empty')}</p>
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
                                        {t(`registry.status_${item.to_status}`)}
                                    </span>
                                    <span className="text-[10px] font-bold text-zinc-400">
                                        {item.from_status || '—'} → {item.to_status}
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

export default function ReviewWorkflow({ agents, statusCounts, api, t }) {
    const [stateFilter, setStateFilter] = useState('all')
    const [expanded, setExpanded] = useState(null)

    const queue = useMemo(() => {
        const rows = agents.map((a) => ({
            id: a.id,
            organization: a.provider?.organization || '—',
            status: a.publishStatus || 'published',
        }))
        rows.sort((a, b) => {
            const ra = STATE_ORDER.indexOf(a.status)
            const rb = STATE_ORDER.indexOf(b.status)
            if (ra !== rb) return ra - rb
            return a.id.localeCompare(b.id)
        })
        if (stateFilter !== 'all') return rows.filter((r) => r.status === stateFilter)
        return rows
    }, [agents, stateFilter])

    const toggle = useCallback((key) => {
        setExpanded((cur) => (cur === key ? null : key))
    }, [])

    return (
        <div className="flex flex-col h-full min-h-0">
            {/* Per-state counters */}
            <div className="shrink-0 grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4 px-2">
                {['all', ...STATE_ORDER].map((state) => {
                    const count = state === 'all' ? agents.length : (statusCounts?.[state] || 0)
                    const active = stateFilter === state
                    return (
                        <button
                            key={state}
                            onClick={() => setStateFilter(state)}
                            className={`p-4 rounded-2xl border text-left transition-all ${
                                active
                                    ? 'border-blue-400 bg-blue-50/60 dark:bg-blue-900/20'
                                    : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-zinc-300 dark:hover:border-zinc-700'
                            }`}
                        >
                            <div className="flex items-center justify-between mb-1.5">
                                <span className="text-[10px] font-black uppercase tracking-wider text-zinc-400">
                                    {state === 'all' ? t('registry.review_state_all') : t(`registry.status_${state}`)}
                                </span>
                                {state !== 'all' && <StatusPill status={state} t={t} />}
                            </div>
                            <span className="text-2xl font-black text-zinc-900 dark:text-white">{count}</span>
                        </button>
                    )
                })}
            </div>

            {/* Review queue */}
            <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0 px-2 pb-8">
                <div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden">
                    <table className="w-full border-collapse text-sm">
                        <thead>
                            <tr className="bg-zinc-50 dark:bg-zinc-800/50">
                                {[t('registry.col_name'), t('registry.col_org'), t('registry.col_status'), ''].map((h, i) => (
                                    <th
                                        key={i}
                                        className="px-4 py-3 text-left text-[11px] font-black uppercase tracking-wider text-zinc-400 dark:text-zinc-500 border-b border-zinc-200 dark:border-zinc-800"
                                    >
                                        {h}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {queue.map((row) => {
                                const key = `${row.organization}/${row.id}`
                                const isOpen = expanded === key
                                return (
                                    <Fragment key={key}>
                                        <tr
                                            onClick={() => toggle(key)}
                                            className="cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/40 transition-colors"
                                        >
                                            <td className="px-4 py-3 font-bold text-zinc-900 dark:text-white">
                                                <span className="inline-flex items-center gap-2">
                                                    <ChevronDown
                                                        size={14}
                                                        className={`text-zinc-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                                                    />
                                                    {row.id}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3 text-xs font-bold text-zinc-500 dark:text-zinc-400">
                                                {row.organization}
                                            </td>
                                            <td className="px-4 py-3">
                                                <StatusPill status={row.status} t={t} />
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                <span className="inline-flex items-center gap-1 text-[10px] font-black uppercase text-zinc-400">
                                                    <History size={12} />
                                                    {t('registry.review_view_trail')}
                                                </span>
                                            </td>
                                        </tr>
                                        {isOpen && (
                                            <tr>
                                                <td colSpan={4} className="bg-zinc-50/60 dark:bg-zinc-900/60 border-b border-zinc-100 dark:border-zinc-800">
                                                    <ReviewTrail
                                                        organization={row.organization}
                                                        name={row.id}
                                                        api={api}
                                                        t={t}
                                                    />
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                )
                            })}
                            {queue.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="py-16 text-center text-zinc-400 text-sm font-bold">
                                        {t('registry.no_agents')}
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
                <p className="mt-3 px-2 flex items-center gap-1.5 text-[10px] font-bold text-zinc-400">
                    <ShieldCheck size={11} />
                    {t('registry.review_readonly_hint')}
                </p>
            </div>
        </div>
    )
}
