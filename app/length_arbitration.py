"""Evidence requests, blind verification and bounded automatic length arbitration.

No HTTP or answer references: every role is dispatched by the existing shared
request queue. Master/expert suggestions remain immutable candidates until a
judge returns a decision. Failures still deliver the original complete ledger.
"""
from copy import deepcopy
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .domain_base import Contract
from .length_context import freeze_context, work_package
from .length_expert import ContextRequest, LengthExpertResponse, assess_reply, target_rows
from .member_recognition import LengthInput, calculate_length
from .native_pdf import write_json

MAX_EVIDENCE_ROUNDS = 2


class EvidenceRequest(ContextRequest):
    view_id: str
    member_ids: list[str] = Field(min_length=1)


class EvidencePlan(Contract):
    requests: list[EvidenceRequest] = Field(max_length=6)


class Verdict(Contract):
    member_id: str
    selected_candidate_id: str | None
    decision: Literal['SUPPORTED', 'INFERRED', 'UNRESOLVED']
    reason: str = Field(min_length=1, max_length=2400)
    evidence_ids: list[str]


class ArbitrationResponse(LengthExpertResponse):
    verdicts: list[Verdict]
    context_requests: list[EvidenceRequest] = Field(default_factory=list, max_length=6)


CONTRACTS = {'length_evidence': EvidencePlan,
             'length_verifier': LengthExpertResponse,
             'length_arbiter': ArbitrationResponse}


def candidate(identity, role, length_input, *, resolution=None, references=None, errors=None):
    parsed = LengthInput.model_validate(length_input) if length_input else None
    value, formula, checks = calculate_length(parsed) if parsed else (None, 'unknown', [])
    return {'candidate_id': f'{identity}:{role}', 'member_id': identity, 'role': role,
            'length_input': parsed.model_dump() if parsed else None,
            'length_mm': value, 'formula': formula, 'resolution': resolution,
            'references': references or [], 'errors': list(errors or []) + checks,
            'selectable': value is not None and not errors and not checks}


def fallback_candidate(candidates, *, rejected=False):
    """Deterministic degraded delivery, explicitly INFERRED, never confirmed.

    Prefer inspectable endpoints/references and agreement by independent roles;
    repeated replies from one role do not create extra votes. Ties preserve
    earlier candidates rather than granting an expert an authority advantage.
    """
    if rejected:
        return None
    valid = [c for c in candidates if c['selectable']]
    def rank(value):
        resolution = value.get('resolution') or {}
        grounded = bool(resolution.get('endpoints') and value['references'])
        roles = {c['role'].rstrip('0123456789') for c in valid
                 if abs(c['length_mm'] - value['length_mm']) <= .01}
        return grounded, len(roles)
    return max(valid, key=rank) if valid else None


class LengthArbitration:
    """Coordinator-owned state machine; methods return the next request or None."""

    def __init__(self, item, all_items, assessment, trace, audit, emit):
        self.item = item
        self.rows = deepcopy(item['rows'])
        ids = item.get('length_target_ids')
        self.targets = list(ids) if ids is not None else [r['id'] for r in target_rows(self.rows)]
        self.audit, self.emit = audit, emit
        self.path = trace / item['view']['id'] / 'arbitration'
        self.catalog = {i['view']['id']: i for i in all_items if 'entry' in i}
        self.candidates = {identity: [] for identity in self.targets}
        self.images = []
        self.feedback = []
        self.round = 0
        self.verifier_rounds = 0
        self.judge_rounds = 0
        self.status = {'status': 'running', 'target_count': len(self.targets),
                       'max_evidence_rounds': MAX_EVIDENCE_ROUNDS, 'evidence_rounds': 0}
        item['status']['length_arbitration'] = self.status
        for row in self.rows:
            if row['id'] not in self.targets:
                continue
            value = row.get('length_input')
            if not value and row.get('length_mm'):
                value = {'method': 'DIRECT', 'direct_mm': row['length_mm'],
                         'evidence': 'Original master candidate, not independently verified'}
            self.candidates[row['id']].append(candidate(row['id'], 'master', value))
        if assessment:
            self.add_assessment('expert', assessment)
        self.save('candidates_initial.json', self.candidates)

    def save(self, name, value):
        write_json(self.path / name, value)

    def add_assessment(self, role, assessment):
        refs = assessment['payload']['references']
        for identity, update in assessment['updates'].items():
            if identity not in self.candidates:
                continue
            resolution = update['resolution']
            self.candidates[identity].append(candidate(identity, role, update['length_input'],
                resolution=resolution,
                references=[r for r in refs if r['id'] in resolution['reference_ids']],
                errors=update['errors']))

    def freeze(self, requests):
        """Only catalog-owned files can be read; models cannot supply paths."""
        feedback = []
        for request in requests:
            request = EvidenceRequest.model_validate(request).model_dump(mode='json')
            source = self.catalog.get(request['view_id'])
            if not source or not set(request['member_ids']) <= set(self.targets):
                feedback.append({'check': 'evidence_scope', 'message': '补图必须来自上传的视图目录，且仅涉及本次目标ID。'})
                continue
            crop = freeze_context(self.item['directory'], request['view_id'],
                                  Path(source['entry']['image_path']), request)
            if any(i['image_file'] == crop['image_file'] for i in self.images):
                continue
            evidence = {**crop, 'evidence_id': f"E{len(self.images)+1}",
                        'view_id': request['view_id'],
                        'plane_name': source['context']['current_view_tag'],
                        'original_size_px': [source['plan']['width_px'], source['plan']['height_px']],
                        'member_ids': request['member_ids']}
            self.images.append(evidence)
            self.audit.event('LENGTH_ARBITRATION_CONTEXT', **evidence)
        self.item['status']['length_expert_context_images'] = [
            *self.item.get('expert_contexts', []), *self.images]
        self.save('evidence_images.json', self.images)
        return feedback

    def package(self, kind):
        # Reuse the blinded identity package; never include candidate numbers in
        # planner or verifier requests. Each request is a fresh conversation.
        entry, text = work_package(self.item)
        metadata = json.loads(text.split('输入工作包：\n', 1)[1])
        metadata.pop('previous_expert_reply', None)
        metadata['uploaded_views'] = [
            {'view_id': i['view']['id'], 'title': i['view']['title'],
             'plane_name': i['context']['current_view_tag'],
             'axis_names': i['context']['axis_names'],
             'original_size_px': [i['plan']['width_px'], i['plan']['height_px']]}
            for i in self.catalog.values()]
        details = list(entry.get('detail_paths', []))
        for evidence in self.images:
            metadata['image_map'].append({'image_number': len(metadata['image_map'])+1,
                                           'role': 'arbitration_evidence', **evidence})
            details.append(str(self.item['directory'] / 'views' / evidence['image_file']))
        entry = {**entry, 'detail_paths': details}
        metadata['evidence_round'] = self.round
        metadata['evidence_rounds_remaining'] = MAX_EVIDENCE_ROUNDS - self.round
        if kind == 'length_arbiter':
            metadata.update(candidates=self.candidates, local_feedback=self.feedback,
                            evidence_images=self.images)
        # Planner's free text is deliberately absent from the blind verifier:
        # even its crop reason can accidentally disclose a numerical hypothesis.
        if kind == 'length_verifier':
            for image in metadata['image_map']:
                image.pop('reason', None)
            metadata['independence_note'] = '独立读取原图；未提供任何旧长度、候选答案或其他代理的判断。'
        prompt = (Path(__file__).parent / 'prompts' / f'{kind}.md').read_text(encoding='utf-8')
        self.save(f'{kind}_input{self.round:02d}.json', metadata)
        return kind, entry, prompt + '\n输入工作包：\n' + json.dumps(metadata, ensure_ascii=False)

    def start(self):
        self.audit.event('LENGTH_ARBITRATION_STARTED', view_id=self.item['view']['id'],
                         targets=self.targets, candidate_count=sum(map(len, self.candidates.values())))
        return self.package('length_evidence')

    def accept(self, kind, payload):
        self.save(f'{kind}_reply{self.round:02d}.json', payload)
        if kind == 'length_evidence':
            self.feedback += self.freeze(payload['requests'])
            return self.package('length_verifier')
        size = [self.item['plan']['width_px'], self.item['plan']['height_px']]
        stripped = {k: payload[k] for k in LengthExpertResponse.model_fields}
        assessment = assess_reply(stripped, self.rows, size, target_ids=self.targets)
        self.save(f'{kind}_assessment{self.round:02d}.json', assessment)
        if kind == 'length_verifier':
            self.verifier_rounds += 1
            self.add_assessment(f'verifier{self.round}', assessment)
            self.feedback = assessment['feedback']
            request = payload.get('context_request')
            if request and self.round < MAX_EVIDENCE_ROUNDS:
                self.feedback += self.freeze([{**request, 'view_id': self.item['view']['id'],
                                                'member_ids': self.targets}])
                self.round += 1
                return self.package('length_verifier')
            return self.package('length_arbiter')
        self.judge_rounds += 1
        self.feedback = assessment['feedback'] + self.check_verdicts(payload)
        requests = list(payload.get('context_requests', []))
        if payload.get('context_request'):
            requests.append({**payload['context_request'], 'view_id': self.item['view']['id'],
                             'member_ids': self.targets})
        if (requests or self.feedback) and self.round < MAX_EVIDENCE_ROUNDS:
            self.feedback += self.freeze(requests)
            self.round += 1
            return self.package('length_arbiter')
        self.finish(payload, assessment)
        return None

    def check_verdicts(self, payload):
        feedback = []
        ids = [v['member_id'] for v in payload['verdicts']]
        if len(ids) != len(set(ids)) or set(ids) != set(self.targets):
            feedback.append({'check': 'verdict_coverage', 'message': 'verdicts必须对每个目标ID恰好返回一次。'})
        image_ids = {'original', *[t['id'] for t in self.item['plan']['tiles']],
                     *[i['evidence_id'] for i in self.images]}
        for verdict in payload['verdicts']:
            candidates = self.candidates.get(verdict['member_id'], [])
            selected = verdict['selected_candidate_id']
            if selected and not any(c['candidate_id'] == selected and c['selectable'] for c in candidates):
                feedback.append({'check': 'candidate_scope', 'member_id': verdict['member_id'],
                                 'message': '只能选择属于本目标且通过本地长度校验的候选，或以null表示重新推导。'})
            if not set(verdict['evidence_ids']) <= image_ids:
                feedback.append({'check': 'image_reference', 'member_id': verdict['member_id'],
                                 'message': 'evidence_ids只能引用original、输入tile ID或E编号。'})
            if verdict['decision'] == 'SUPPORTED' and not verdict['evidence_ids']:
                feedback.append({'check': 'missing_visual_evidence', 'member_id': verdict['member_id'],
                                 'message': 'SUPPORTED裁决必须指出图像证据。'})
            results = [r for r in payload['results'] if r['member_id'] == verdict['member_id']]
            if verdict['decision'] == 'SUPPORTED' and (len(results) != 1 or results[0]['status'] != 'CONFIRMED'):
                feedback.append({'check': 'unsupported_final_expression', 'member_id': verdict['member_id'],
                                 'message': 'SUPPORTED必须提供对应的CONFIRMED结果及完整表达式，不能仅选候选或返回UNKNOWN。'})
        return feedback

    def failed(self, kind, message):
        self.audit.event('LENGTH_ARBITRATION_ROLE_FAILED', view_id=self.item['view']['id'],
                         kind=kind, error=message)
        self.feedback.append({'check': 'request_failure', 'message': message})
        if kind == 'length_evidence':
            return self.package('length_verifier')
        if kind == 'length_verifier':
            return self.package('length_arbiter')
        self.finish(None, None)
        return None

    def finish(self, payload, assessment):
        verdicts = {}
        for verdict in (payload or {}).get('verdicts', []):
            verdicts.setdefault(verdict['member_id'], []).append(verdict)
        revised = deepcopy(self.rows)
        changes = []
        for row in revised:
            identity = row['id']
            if identity not in self.targets:
                continue
            candidates = self.candidates[identity]
            single = verdicts.get(identity, [])
            verdict = single[0] if len(single) == 1 else None
            errors = [f for f in self.feedback if f.get('member_id') in (None, identity)]
            valid = verdict is not None and not errors
            rejected = valid and verdict['decision'] == 'UNRESOLVED'
            chosen = None
            update = assessment['updates'].get(identity) if assessment else None
            if valid and update and update['length_input'] and not update['errors'] and verdict['decision'] != 'UNRESOLVED':
                # The final expression, not a candidate's persuasive prose, is
                # the accepted arithmetic. Judge may derive a third solution.
                chosen = candidate(identity, 'arbiter', update['length_input'],
                    resolution=update['resolution'],
                    references=[r for r in payload['references'] if r['id'] in update['resolution']['reference_ids']])
            elif valid and verdict['decision'] == 'INFERRED' and verdict['selected_candidate_id']:
                chosen = next((c for c in candidates if c['candidate_id'] == verdict['selected_candidate_id'] and c['selectable']), None)
            decision = verdict['decision'] if chosen else 'INFERRED'
            if not chosen:
                # Automatic delivery even if the judge failed. These are
                # explicitly inferred fallbacks, never silently confirmed.
                chosen = fallback_candidate(candidates, rejected=rejected)
            before = row.get('length_mm')
            if chosen:
                row['length_input'] = deepcopy(chosen['length_input'])
                row['length_mm'], row['length_formula'], _ = calculate_length(LengthInput.model_validate(row['length_input']))
                resolution = chosen.get('resolution')
                if resolution and resolution.get('endpoints'):
                    row['endpoints'] = [e['name'] for e in resolution['endpoints']]
                row['segmentation_status'] = 'CONFIRMED' if decision == 'SUPPORTED' else row.get('segmentation_status', 'UNRESOLVED')
            else:
                decision = 'UNRESOLVED'
                if rejected:
                    # A valid judge rejection must not resurrect an earlier
                    # numeric guess via the delivery fallback. Keep that guess
                    # only in candidates/before, not in the deduplication key.
                    row['length_mm'] = None
                    row['length_input'] = {'method': 'UNKNOWN', 'direct_mm': None,
                        'horizontal_mm': None, 'vertical_mm': None, 'evidence': verdict['reason'][:1000]}
                    row['length_formula'] = 'unknown'
            row['length_expert_checks'] = [] if decision == 'SUPPORTED' else [
                '长度自动仲裁：采用证据最充分的推断候选。' if chosen else '长度自动仲裁未取得有效尺寸；构件保留，长度未知。']
            row['length_arbitration'] = {'status': decision,
                'selected_candidate_id': chosen['candidate_id'] if chosen else None,
                'verdict': verdict, 'candidates': candidates,
                'final_expression': chosen, 'local_feedback': errors,
                'before_mm': before, 'after_mm': row.get('length_mm'),
                'automatic': True, 'manual_required': False,
                'note': '本地校验只证明引用与计算一致；视觉对应由模型判断。'}
            row['length_review'] = {'status': decision, 'applied': chosen is not None or (rejected and before is not None),
                'before': {k: deepcopy(next(r for r in self.rows if r['id'] == identity).get(k))
                           for k in ('length_mm', 'length_input', 'endpoints', 'issue', 'segmentation_status')},
                'resolution': chosen.get('resolution') if chosen else None,
                'references': chosen.get('references', []) if chosen else [],
                'arbitration': True, 'arithmetic_only': False}
            changes.append({'member_id': identity, 'status': decision,
                            'before_mm': before, 'after_mm': row.get('length_mm')})
            self.audit.event('LENGTH_ARBITRATION_DECIDED', view_id=self.item['view']['id'],
                             **changes[-1], candidate_id=row['length_arbitration']['selected_candidate_id'])
        self.item['rows'] = revised
        self.status.update(status='completed_with_warnings' if any(c['status'] != 'SUPPORTED' for c in changes) else 'completed',
            verifier_rounds=self.verifier_rounds, judge_rounds=self.judge_rounds,
            evidence_rounds=self.round, evidence_images=len(self.images),
            supported=sum(c['status'] == 'SUPPORTED' for c in changes),
            inferred=sum(c['status'] == 'INFERRED' for c in changes),
            unresolved=sum(c['status'] == 'UNRESOLVED' for c in changes),
            unknown_remaining=sum(r.get('length_mm') is None for r in revised),
            changes=changes, feedback=self.feedback)
        self.item['status'].update(status='completed_with_warnings' if self.status['status'] != 'completed' or
            self.item['status'].get('remaining_checks') else 'completed', model_calls=self.item['calls'])
        self.save('result.json', {'verdict': payload, 'status': self.status, 'rows': revised})
        self.emit(f"自动仲裁完成：{self.item['view']['title']}，有依据 {self.status['supported']}，推断 {self.status['inferred']}，长度未知 {self.status['unknown_remaining']}；无需人工继续。")
