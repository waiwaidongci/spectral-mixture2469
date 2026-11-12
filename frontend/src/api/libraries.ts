import {request} from './client';import {Library} from '../types/domain';export const getLibraries=()=>request<Library[]>('/libraries');
